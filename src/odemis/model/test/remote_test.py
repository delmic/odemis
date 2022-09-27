#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''
Created on 18 Jun 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms 
of the GNU General Public License version 2 as published by the Free Software 
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; 
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR 
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with 
Odemis. If not, see http://www.gnu.org/licenses/.
'''
import Pyro4
from concurrent import futures
from concurrent.futures import CancelledError
import gc
import logging
import numpy
from odemis import model
from odemis.model import roattribute, oneway, isasync, VigilantAttributeBase
from odemis.util import mock, timeout, executeAsyncTask
import os
import pickle
import sys
import threading
import time
import unittest
from multiprocessing import Process

logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)-15s: %(message)s")
logging.getLogger().setLevel(logging.DEBUG)
#gc.set_debug(gc.DEBUG_LEAK | gc.DEBUG_STATS)

pyrolog = logging.getLogger("Pyro4")
pyrolog.setLevel(min(pyrolog.getEffectiveLevel(), logging.DEBUG))

# Use processes or threads? Threads are easier to debug, but less real
USE_THREADS = True

# @unittest.skip("simple")
class ContainerTest(unittest.TestCase):
    def test_empty_container(self):
        container = model.createNewContainer("testempty")
        container.ping()
        container.terminate()

    def test_instantiate_simple_component(self):
        container, comp = model.createInNewContainer("testscont", FamilyValueComponent, {"name":"MyComp"})
        self.assertEqual(comp.name, "MyComp")

        comp_prime = model.getObject("testscont", "MyComp")
        self.assertEqual(comp_prime.name, "MyComp")

        comp.terminate()
        container.terminate()

    def test_instantiate_component(self):
        container, comp = model.createInNewContainer("testcont", MyComponent, {"name":"MyComp"})
        self.assertEqual(comp.name, "MyComp")
        val = comp.my_value
        self.assertEqual(val, "ro", "Reading attribute failed")

        comp_prime = model.getObject("testcont", "MyComp")
        self.assertEqual(comp_prime.name, "MyComp")

        container.ping()

        # check getContainer works
        container2 = model.getContainer("testcont")
        container2.ping()
        self.assertEqual(container, container2)

        comp.terminate()
        container.terminate()

    def test_multi_components(self):
        container, comp = model.createInNewContainer("testmulti", FatherComponent, {"name":"Father", "children_num":3})
        self.assertEqual(comp.name, "Father")
        self.assertEqual(len(comp.children.value), 3, "Component should have 3 children")

        for child in comp.children.value:
            self.assertLess(child.value, 3)
            comp_direct = model.getObject("testmulti", child.name)
            self.assertEqual(comp_direct.name, child.name)
#            child.terminate()

        comp.terminate()
        # we are not terminating the children, but this should be caught by the container
        container.terminate()

    def test_timeout(self):
        if Pyro4.config.COMMTIMEOUT == 0 or Pyro4.config.COMMTIMEOUT > 20:
            self.skipTest("Timeout too long (%d s) to test." % Pyro4.config.COMMTIMEOUT)
        server = threading.Thread(target=ServerLoop, args=("backend",))
        server.start()
        time.sleep(0.1) # give it some time to start

        rdaemon = Pyro4.Proxy("PYRO:Pyro.Daemon@./u:backend")
        rdaemon.ping()
        time.sleep(Pyro4.config.COMMTIMEOUT + 2)
        rdaemon.ping()


# @unittest.skip("simple")
class SerializerTest(unittest.TestCase):

    def test_recursive(self):
        try:
            os.remove("test")
        except OSError:
            pass
        daemon = Pyro4.Daemon(unixsocket="test")
        childc = FamilyValueComponent("child", 43, daemon=daemon)
        parentc = FamilyValueComponent("parent", 42, children={"one": childc}, daemon=daemon)
        childc.parent = parentc
#        childc.parent = None

        dump = pickle.dumps(parentc, pickle.HIGHEST_PROTOCOL)
#        print "dump size is", len(dump)
        parentc_unpickled = pickle.loads(dump)
        self.assertEqual(parentc_unpickled.value, 42)

    def test_mock(self):
        try:
            os.remove("test")
        except OSError:
            pass
        daemon = Pyro4.Daemon(unixsocket="test")
        CONFIG_SED = {"name": "sed", "role": "sed", "channel": 5, "limits": [-3, 3]}
        CONFIG_SCANNER = {"name": "scanner", "role": "ebeam", "limits": [[0, 5], [0, 5]]}
        CONFIG_SEM = {"name": "sem", "role": "sem", "device": "/dev/comedi0",
              "children": {"detector0": CONFIG_SED, "scanner": CONFIG_SCANNER}
        }
        sem = mock.MockComponent(daemon=daemon, _realcls=model.HwComponent, **CONFIG_SEM)

        dump = pickle.dumps(sem, pickle.HIGHEST_PROTOCOL)
#        print "dump size is", len(dump)
        sem_unpickled = pickle.loads(dump)
        # Warning, we cannot check inside children because it's (now) a VA,
        # which requires the daemon to be actually running
        self.assertIsInstance(sem_unpickled.children, model.VigilantAttributeBase)
        self.assertEqual(sem_unpickled.name, "sem")
        sem.terminate()
        daemon.shutdown()


# @unittest.skip("simple")
class ProxyOfProxyTest(unittest.TestCase):
# Test sharing a shared component from the client

#  create one remote container with an object (Component)
#  create a second remote container with a HwComponent
#  change .affects of HwComponent to the first object
    def test_component(self):
        cont, comp = model.createInNewContainer("testscont", model.HwComponent,
                                          {"name":"MyHwComp", "role":"affected"})
        self.assertEqual(comp.name, "MyHwComp")

        cont2, comp2 = model.createInNewContainer("testscont2", model.HwComponent,
                                           {"name":"MyHwComp2", "role":"affecter"})
        self.assertEqual(comp2.name, "MyHwComp2")

        comp2.affects.value.append(comp)
        self.assertEqual(len(comp2.affects.value), 1)
        for c in comp2.affects.value:
            self.assertTrue(isinstance(c, model.ComponentBase))
            self.assertEqual(c.name, "MyHwComp")
            self.assertEqual(c.role, "affected")

        comp2_new = model.getObject("testscont2", "MyHwComp2")
        self.assertEqual(comp2_new.name, "MyHwComp2")
        self.assertEqual(len(comp2_new.affects.value), 1)

        comp.terminate()
        comp2.terminate()
        cont.terminate()
        cont2.terminate()
        time.sleep(0.1) # give it some time to terminate

    def test_va(self):
        cont, comp = model.createInNewContainer("testscont", SimpleHwComponent,
                                          {"name":"MyHwComp", "role":"affected"})
        self.assertEqual(comp.name, "MyHwComp")

        cont2, comp2 = model.createInNewContainer("testscont2", model.HwComponent,
                                           {"name":"MyHwComp2", "role":"affecter"})
        self.assertEqual(comp2.name, "MyHwComp2")

        comp2.affects.value.append(comp)
        self.assertEqual(len(comp2.affects.value), 1)
        comp_indir = comp2.affects.value[0]
        self.assertIsInstance(comp_indir.prop, VigilantAttributeBase)
        self.assertIsInstance(comp_indir.cont, VigilantAttributeBase)
        self.assertIsInstance(comp_indir.enum, VigilantAttributeBase)

        prop = comp_indir.prop
        self.assertEqual(prop.value, 42)
        prop.value += 1
        self.assertEqual(prop.value, 43)
        self.assertEqual(comp.prop.value, 43)

        self.assertEqual(comp_indir.cont.value, 2.0)
        self.assertIsInstance(comp_indir.cont.range, tuple)
        try:
            # there is no such thing, it should fail
            c = len(comp_indir.cont.choices)
            self.fail("Accessing choices should fail")
        except:
            pass

        self.assertEqual(comp_indir.enum.value, "a")

        comp.terminate()
        comp2.terminate()
        cont.terminate()
        cont2.terminate()

    def test_roattributes(self):
        cont, comp = model.createInNewContainer("testscont", MyComponent,
                                                {"name": "MyComp"})
        self.assertEqual(comp.name, "MyComp")

        cont2, comp2 = model.createInNewContainer("testscont2", model.HwComponent,
                                                  {"name": "MyHwComp2", "role": "affecter"})
        self.assertEqual(comp2.name, "MyHwComp2")

        comp2.affects.value.append(comp)
        self.assertEqual(len(comp2.affects.value), 1)
        for c in comp2.affects.value:
            self.assertTrue(isinstance(c, model.ComponentBase))
            self.assertEqual(c.name, "MyComp")
            val = comp.my_value
            self.assertEqual(val, "ro", "Reading attribute failed")

        comp.terminate()
        comp2.terminate()
        cont.terminate()
        cont2.terminate()

    @timeout(20)
    def test_dataflow(self):
        cont, comp = model.createInNewContainer("testscont", MyComponent,
                                          {"name":"MyComp"})
        self.assertEqual(comp.name, "MyComp")

        cont2, comp2 = model.createInNewContainer("testscont2", model.HwComponent,
                                           {"name":"MyHwComp2", "role":"affecter"})
        self.assertEqual(comp2.name, "MyHwComp2")

        comp2.affects.value.append(comp)
        self.assertEqual(len(comp2.affects.value), 1)
        comp_indir = list(comp2.affects.value)[0]

        self.count = 0
        self.data_arrays_sent = 0
        comp_indir.data.reset()

        comp_indir.data.subscribe(self.receive_data)
        time.sleep(0.5)
        comp_indir.data.unsubscribe(self.receive_data)
        count_end = self.count
        print("received %d arrays over %d" % (self.count, self.data_arrays_sent))

        time.sleep(0.1)
        self.assertEqual(count_end, self.count)

        comp.terminate()
        comp2.terminate()
        cont.terminate()
        cont2.terminate()
        time.sleep(0.1) # give it some time to terminate

    @timeout(20)
    def test_dataflow_unsub(self):
        """
        Check the dataflow is automatically unsubscribed when the subscriber
        disappears.
        """
        cont, comp = model.createInNewContainer("testscont", MyComponent,
                                                {"name": "MyComp"})
        cont2, comp2 = model.createInNewContainer("testscont2", MyComponent,
                                                  {"name": "MyComp2"})

        self.count = 0
        self.data_arrays_sent = 0
        comp.data.reset()
        # special hidden function that will directly ask the original DF
        nlisteners = comp.data._count_listeners()
        self.assertEqual(nlisteners, 0)

        comp2.sub(comp.data)
        time.sleep(0.5)
#         comp2.unsub()
        count_arrays = comp2.get_data_count()
        logging.info("received %d arrays", count_arrays)
        nlisteners = comp.data._count_listeners()
        logging.info("orig has %d listeners", nlisteners)
        comp2.terminate()
        cont2.terminate()
        logging.info("comp2 should now be disappeared")

        time.sleep(0.4)
        nlisteners = comp.data._count_listeners()
        self.assertEqual(nlisteners, 0)

        comp.terminate()
        cont.terminate()
        time.sleep(0.1) # give it some time to terminate

    def receive_data(self, dataflow, data):
        self.count += 1
        self.assertEqual(data.shape, (2048, 2048))
        self.data_arrays_sent = data[0][0]
        self.assertGreaterEqual(self.data_arrays_sent, self.count)

# @unittest.skip("simple")
class RemoteTest(unittest.TestCase):
    """
    Test the Component, DataFlow, and VAs when shared remotely.
    The test cases are run as "clients" and at start a server is started.
    """
    container_name = "test"

    def setUp(self):
        # Use Thread for debug:
        if USE_THREADS:
            self.server = threading.Thread(target=ServerLoop, args=(self.container_name,))
        else:
            self.server = Process(target=ServerLoop, args=(self.container_name,))
        self.server.start()

        self.count = 0
        self.data_arrays_sent = 0
        time.sleep(0.1) # give it some time to start
        self.rdaemon = Pyro4.Proxy("PYRO:Pyro.Daemon@./u:" + self.container_name)
        self.comp = self.rdaemon.getObject("mycomp")

    def tearDown(self):
        self.comp.stopServer()
        time.sleep(0.1) # give it some time to terminate

        if self.server.is_alive():
            if not USE_THREADS:
                print("Warning: killing server still alive")
                self.server.terminate()

#    @unittest.skip("simple")
    def test_simple(self):
        """
        start a component, ping, and stop it
        """

        ret = self.comp.ping()
        self.assertEqual(ret, "pong", "Ping failed")

#    @unittest.skip("simple")
    def test_exception(self):

        # test it raises
        self.assertRaises(MyError, self.comp.bad_call)

        # test it raises when wrong argument
        self.assertRaises(TypeError, self.comp.ping, ("non needed arg",))

        # non existing method
        self.assertRaises(AttributeError, self.comp.non_existing_method)


#    @unittest.skip("simple")
    def test_roattributes(self):
        """
        check roattributes
        """
        val = self.comp.my_value
        self.assertEqual(val, "ro", "Reading attribute failed")

#    @unittest.skip("simple")
    def test_async(self):
        """
        test futures
        MyComponent queues the future in order of request
        """
        self.comp.set_number_futures(0)

        ft1 = self.comp.do_long(2) # long enough we can cancel ft2
        ft2 = self.comp.do_long(1) # shorter than ft1
        self.assertFalse(ft1.done(), "Future finished too early")
        self.assertFalse(ft2.done(), "Future finished too early")
        self.assertFalse(ft2.cancelled(), "future doesn't claim being cancelled")
        self.assertFalse(ft2.cancelled(), "future doesn't claim being cancelled")
        self.assertGreater(ft2.result(), 1) # wait for ft2
        self.assertFalse(ft2.cancel(), "could cancel the finished future")

        self.assertTrue(ft1.done(), "Future not finished")
        self.assertGreater(ft1.result(), 2)

        self.assertEqual(self.comp.get_number_futures(), 2)

#    @unittest.skip("simple")
    def test_unref_futures(self):
        """
        test many futures which don't even get referenced
        It should behave as if the function does not return anything
        """
        self.comp.set_number_futures(0)

        expected = 100 # there was a bug with expected > threadpool size (=24)
        start = time.time()
        for i in range(expected):
            self.comp.do_long(0.1)

        ft_last = self.comp.do_long(0.1)
        ft_last.result()
        duration = time.time() - start
        self.assertGreaterEqual(duration, expected * 0.1)

        self.assertEqual(self.comp.get_number_futures(), expected + 1)

#    @unittest.skip("simple")
    def test_ref_futures(self):
        """
        test many futures which get referenced and accumulated
        It should behave as if the function does not return anything
        """
        self.comp.set_number_futures(0)
        small_futures = []

        expected = 100 # there was a bug with expected > threadpool size (=24)
        start = time.time()
        for i in range(expected):
            small_futures.append(self.comp.do_long(0.1))

        ft_last = self.comp.do_long(0.1)
        ft_last.result()
        duration = time.time() - start
        self.assertGreaterEqual(duration, expected * 0.1)

        for f in small_futures:
            self.assertTrue(f.done())

        self.assertEqual(self.comp.get_number_futures(), expected + 1)

#    @unittest.skip("simple")
    def test_async_cancel(self):
        """
        test futures
        """
        self.comp.set_number_futures(0)

        ft1 = self.comp.do_long(2) # long enough we can cancel ft2
        ft2 = self.comp.do_long(1) # shorter than ft1
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

        self.assertEqual(self.comp.get_number_futures(), 2)

    def test_prog_future(self):
        """
        Test ProgressiveFuture (remotely)
        """
        self._done_calls = 0
        self._progess_calls = 0
        self._start = None
        self._end = None
        ft = self.comp.do_progressive_long(5)
        ft.add_update_callback(self._on_future_proress)
        ft.add_done_callback(self._on_future_done)

        # we should have received at least one progress update already
        self.assertGreaterEqual(self._progess_calls, 1)

        ft.result()
        self.assertGreaterEqual(self._progess_calls, 5)
        self.assertEqual(self._done_calls, 1)

    def _on_future_done(self, f):
        self._done_calls += 1

    def _on_future_proress(self, f, start, end):
        self._progess_calls += 1
        logging.info("Received future update for %f -> %f", start, end)

        self._start = start
        self._end = end

#    @unittest.skip("simple")
    def test_subcomponents(self):
        # via method and via roattributes
        # need to test cycles

        p = self.rdaemon.getObject("parent")
        self.assertEqual(len(p.children.value), 1, "parent doesn't have one child")
        c = list(p.children.value)[0]
#        self.assertEqual(c.parent, p, "Component and parent of child is different")
        self.assertEqual(p.value, 42)
        self.assertEqual(c.value, 43)
        self.assertEqual(len(c.children.value), 0, "child shouldn't have children")

#    @unittest.skip("simple")
    def test_dataflow_subscribe(self):
        self.count = 0
        self.expected_shape = (2048, 2048)
        self.data_arrays_sent = 0
        self.comp.data.reset()

        self.comp.data.subscribe(self.receive_data)
        time.sleep(0.5)
        self.comp.data.unsubscribe(self.receive_data)
        count_end = self.count
        print("received %d arrays over %d" % (self.count, self.data_arrays_sent))

        time.sleep(0.1)
        self.assertEqual(count_end, self.count)
        self.assertGreaterEqual(count_end, 1)

    def test_synchronized_df(self):
        """
        Tests 2 dataflows, one synchronized on the event of acquisition started
        on the other dataflow.
        """ 
        number = 20
        self.count = 0
        self.left = number
        self.expected_shape = (2, 2)
        self.expected_shape_au = (2048, 2048)
        self.data_arrays_sent = 0
        dfe = self.comp.data
        dfs = self.comp.datas
        dfe.reset()

        dfs.synchronizedOn(self.comp.startAcquire)
        dfs.subscribe(self.receive_data)

        time.sleep(0.2) # long enough to be after the first data if it generates data
        # ensure that datas hasn't generated anything yet
        self.assertEqual(self.count, 0)

        dfe.subscribe(self.receive_data_auto_unsub)
        for i in range(number):
            # end early if it's already finished
            if self.left == 0:
                break
            time.sleep(0.2) # 0.2s should be more than enough in any case

        self.assertEqual(0, self.left)
        self.assertEqual(number, self.count)
        print("received %d arrays over %d" % (self.count, self.data_arrays_sent))
        max_lat = dfs.get_max_lat()
        if max_lat:
            print("latency: %r, max= %f, avg= %f" % (max_lat, max(max_lat), sum(max_lat)/len(max_lat)))

        time.sleep(0.1)
        self.assertEqual(number, self.count)
        dfs.unsubscribe(self.receive_data)
        dfs.synchronizedOn(None)
        time.sleep(0.1)
        self.assertEqual(number, self.count)

    def test_hw_trigger(self):
        """
        Check that the hw trigger can be detected as hardware trigger, and cannot
        be used for software synchronization
        """
        dfs = self.comp.datas

        # Not allowed to use a HwTrigger to notify (via software call)
        with self.assertRaises(ValueError):
            self.comp.hwTrigger.notify()

        # Check get_type() works as expected
        self.assertTrue(issubclass(self.comp.hwTrigger.get_type(), model.HwTrigger))
        self.assertTrue(issubclass(self.comp.hwTrigger.get_type(), model.Event))

        # This should be fine (but cannot be tested more)
        dfs.synchronizedOn(self.comp.hwTrigger)

        self.assertEqual(dfs.get_event_type(), "hw")

        dfs.synchronizedOn(self.comp.startAcquire)
        self.assertEqual(dfs.get_event_type(), "sw")

        dfs.synchronizedOn(None)
        self.assertEqual(dfs.get_event_type(), None)

#    @unittest.skip("simple")
    def test_dataflow_stridden(self):
        # test that stridden array can be passed (even if less efficient)
        self.count = 0
        self.data_arrays_sent = 0
        self.expected_shape = (2048, 2045)
        self.comp.cut.value = 3
        self.comp.data.reset()

        self.comp.data.subscribe(self.receive_data)
        time.sleep(0.5)
        self.comp.data.unsubscribe(self.receive_data)
        self.comp.cut.value = 0 # put it back
        count_end = self.count
        print("received %d stridden arrays over %d" % (self.count, self.data_arrays_sent))

        time.sleep(0.1)
        self.assertEqual(count_end, self.count)
        self.assertGreaterEqual(count_end, 1)

    def test_dataflow_empty(self):
        """
        test passing empty DataArray
        """
        self.count = 0
        self.data_arrays_sent = 0
        self.comp.data.setShape((0,), 16)
        self.expected_shape = (0,)

        self.comp.data.subscribe(self.receive_data)
        time.sleep(0.5)
        self.comp.data.unsubscribe(self.receive_data)
        count_end = self.count
        print("received %d stridden arrays over %d" % (self.count, self.data_arrays_sent))

        time.sleep(0.1)
        self.assertEqual(count_end, self.count)
        self.assertGreaterEqual(count_end, 1)

    def receive_data(self, dataflow, data):
        self.count += 1
        self.assertEqual(data.shape, self.expected_shape)
        if data.ndim >= 2:
            self.data_arrays_sent = data[0][0]
            self.assertGreaterEqual(self.data_arrays_sent, self.count)

    def receive_data_auto_unsub(self, dataflow, data):
        """
        callback for df
        """
        self.assertEqual(data.shape, self.expected_shape_au)
        self.left -= 1
        if self.left <= 0:
            dataflow.unsubscribe(self.receive_data_auto_unsub)

    def receive_data_and_unsubscribe(self, dataflow, data):
        self.count += 1
        self.assertEqual(data.shape, (2048, 2048))
        self.data_arrays_sent = data[0][0]
        self.assertGreaterEqual(self.data_arrays_sent, self.count)
        dataflow.unsubscribe(self.receive_data_and_unsubscribe)

#    @unittest.skip("simple")
    def test_dataflow_unsubscribe_from_callback(self):
        self.count = 0
        self.data_arrays_sent = 0
        self.comp.data.reset()

        self.comp.data.subscribe(self.receive_data_and_unsubscribe)
        time.sleep(0.3)
        self.assertEqual(self.count, 1)
        # It should be 1, or if the generation went very fast, it might be bigger
        self.assertGreaterEqual(self.data_arrays_sent, 1)
#        print "received %d arrays over %d" % (self.count, self.data_arrays_sent)

#        data = comp.data
#        del comp
#        print gc.get_referrers(data)
#        gc.collect()
#        print gc.get_referrers(data)


#    @unittest.skip("simple")
    def test_dataflow_get(self):
        self.comp.data.reset()
        array = self.comp.data.get()
        self.assertEqual(array.shape, (2048, 2048))
        self.assertEqual(array[0][0], 0)

        array = self.comp.data.get()
        self.assertEqual(array.shape, (2048, 2048))
        self.assertEqual(array[0][0], 0)

#    @unittest.skip("simple")
    def test_va_update(self):
        prop = self.comp.prop
        self.assertIsInstance(prop, VigilantAttributeBase)
        self.assertEqual(prop.value, 42)
        prop.value += 1
        self.assertEqual(prop.value, 43)

        self.called = 0
        self.last_value = None
        prop.subscribe(self.receive_va_update)
        time.sleep(0.01)  # It can take some time to subscribe

        # now count
        prop.value = 3 # +1
        prop.value = 0 # +1
        prop.value = 0 # nothing because same value
        time.sleep(0.1) # give time to receive notifications
        prop.unsubscribe(self.receive_va_update)

        self.assertEqual(prop.value, 0)
        self.assertEqual(self.last_value, 0)
        # called once or twice depending if the brief 3 was seen
        self.assertTrue(1 <= self.called <= 2)
        called_before = self.called

        # check we are not called anymore
        prop.value = 3 # +1
        self.assertEqual(self.called, called_before)

        # re-subscribe
        prop.subscribe(self.receive_va_update)
        time.sleep(0.01)  # It can take some time to subscribe

        # change remotely
        self.comp.change_prop(45)
        time.sleep(0.1) # give time to receive notifications
        self.assertEqual(prop.value, 45)
        prop.unsubscribe(self.receive_va_update)
        self.assertEqual(self.called, called_before + 1)

        try:
            prop.value = 7.5
            self.fail("Assigning float to a int should not be allowed.")
        except TypeError:
            pass # as it should be

    def receive_va_update(self, value):
        logging.debug("Update va to %s", value)
        self.called += 1
        self.last_value = value
        self.assertIsInstance(value, (int, float))

    def test_va_override(self):
        self.comp.prop.value = 42
        with self.assertRaises(AttributeError):
            # Simulate typo of "self.comp.prop.value = 42"
            self.comp.prop = 42

#    @unittest.skip("simple")
    def test_enumerated_va(self):
        # enumerated
        self.assertEqual(self.comp.enum.value, "a")
        self.assertEqual(self.comp.enum.choices, {"a", "c", "bfds"})
        self.comp.enum.value = "c"
        self.assertEqual(self.comp.enum.value, "c")

        try:
            self.comp.enum.value = "wfds"
            self.fail("Assigning out of bound should not be allowed.")
        except IndexError:
            pass # as it should be

    def test_continuous_va(self):
        # continuous
        self.assertEqual(self.comp.cont.value, 2)
        self.assertEqual(self.comp.cont.range, (-1, 3.4))

        self.comp.cont.value = 3.0
        self.assertEqual(self.comp.cont.value, 3)

        try:
            self.comp.cont.value = 4.0
            self.fail("Assigning out of bound should not be allowed.")
        except IndexError:
            pass # as it should be

    def test_list_va(self):
        # List
        l = self.comp.listval
        self.assertEqual(len(l.value), 2)
        self.called = 0

        l.subscribe(self.receive_listva_update)
        time.sleep(0.01)  # It can take some time to subscribe

        l.value += [3]
        self.assertEqual(len(l.value), 3)
        time.sleep(0.01)
        self.assertEqual(self.called, 1)

        l.value[-1] = 4
        self.assertEqual(l.value[-1], 4)
        time.sleep(0.01)
        self.assertEqual(self.called, 2)

        l.value.reverse()
        self.assertEqual(l.value[0], 4)
        time.sleep(0.1)
        self.assertEqual(self.called, 3)
        l.unsubscribe(self.receive_listva_update)

    def receive_listva_update(self, value):
        logging.debug("listva changed to %s", value)
        self.called += 1
        self.last_value = value
        self.assertIsInstance(value, list)

# a basic server (component container)
def ServerLoop(socket_name):
    try:
        os.remove(socket_name)
    except OSError:
        pass
    daemon = Pyro4.Daemon(unixsocket=socket_name)
    component = MyComponent("mycomp", daemon)
#    component = SimpleComponent("simpcomp", daemon=daemon)
    childc = FamilyValueComponent("child", 43, daemon=daemon)
    parentc = FamilyValueComponent("parent", 42, parent=None, children={"one": childc}, daemon=daemon)
    childc.parent = parentc
    daemon.requestLoop()
    component.terminate()
    parentc.terminate()
    daemon.close()


class MyError(Exception):
    pass

class SimpleComponent(model.Component):
    """
    A component that does nothing
    """
    def __init__(self, *args, **kwargs):
        model.Component.__init__(self, *args, **kwargs)

    def ping(self):
        return "pong"

class SimpleHwComponent(model.HwComponent):
    """
    A Hw component that does nothing
    """
    def __init__(self, *args, **kwargs):
        model.HwComponent.__init__(self, *args, **kwargs)
        self.data = FakeDataFlow()
        # TODO automatically register the property when serializing the Component
        self.prop = model.IntVA(42)
        self.cont = model.FloatContinuous(2.0, [-1, 3.4])
        self.enum = model.StringEnumerated("a", {"a", "c", "bfds"})

    @roattribute
    def my_value(self):
        return "ro"


class MyComponent(model.Component):
    """
    A component that does everything
    """
    def __init__(self, name, daemon):
        model.Component.__init__(self, name=name, daemon=daemon)
        self.executor = futures.ThreadPoolExecutor(max_workers=1)
        self.number_futures = 0
        self.startAcquire = model.Event() # triggers when the acquisition of .data starts
        self.hwTrigger = model.HwTrigger()
        self.data = FakeDataFlow(sae=self.startAcquire)
        self.datas = SynchronizableDataFlow()

        self.data_count = 0
        self._df = None

        # TODO automatically register the property when serializing the Component
        self.prop = model.IntVA(42)
        self.cont = model.FloatContinuous(2.0, [-1, 3.4], unit="C")
        self.enum = model.StringEnumerated("a", {"a", "c", "bfds"})
        self.cut = model.IntVA(0, setter=self._setCut)
        self.listval = model.ListVA([2, 65])

    def _setCut(self, value):
        self.data.cut = value
        return self.data.cut

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

    # oneway to ensure that it will be set in a different thread than the call
    @oneway
    def change_prop(self, value):
        """
        set a new value for the VA prop
        """
        self.prop.value = value

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
        return time.time() - start

    @isasync
    def do_progressive_long(self, duration=5):
        """
        return a ProgressiveFuture, which will have the estimated time shorten
        """
        # First estimate the time to 10s, and then it will be shorten
        start = time.time() + 1
        end = start + 10
        f = model.ProgressiveFuture(start, end)

        # run in a separate thread
        executeAsyncTask(f, self._long_pessimistic_task, args=(f, duration))
        return f

    def _long_pessimistic_task(self, future, duration):
        start = time.time()
        end = start + duration
        psmt_end = start + 2 * duration + 1
        while time.time() < end:
            time.sleep(1)
            psmt_end = max(psmt_end - 1, time.time())
            future.set_progress(end=psmt_end)

        return time.time() - start

    def get_number_futures(self):
        return self.number_futures

    def set_number_futures(self, value):
        self.number_futures = value

    def _on_end_long(self, future):
        self.number_futures += 1

    def sub(self, df):
        self._df = df
        df.subscribe(self.data_receive)

    def unsub(self):
        self._df.unsubscribe(self.data_receive)

    def data_receive(self, df, data):
        logging.info("Received data of shape %r", data.shape)
        self.data_count += 1

    def get_data_count(self):
        return self.data_count

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
            self.children.value.add(child)

    @roattribute
    def value(self):
        return self._value


class FakeDataFlow(model.DataFlow):
    def __init__(self, sae=None, *args, **kwargs):
        super(FakeDataFlow, self).__init__(*args, **kwargs)
        self.shape = (2048, 2048)
        self.bpp = 16
        self._stop = threading.Event()
        self._thread = None
        self.count = 0
        self.cut = 0 # to test non stride arrays
        self._startAcquire = sae

    def _create_one(self, shape, bpp, index):
#        print self.startAcquire
        if self._startAcquire:
            self._startAcquire.notify()
            time.sleep(0.1) # if test events => simulate slow acquisition
        array = numpy.zeros(shape, dtype=("uint%d" % bpp)).view(model.DataArray)
        if shape[0] > 0:
            array[index % shape[0], :] = 255
        if self.cut:
            return array[:, self.cut:]
        else:
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
        if len(array):
            array[0][0] = 0
        return array

    def start_generate(self):
        self.count = 0 # reset
        assert self._thread is None
        self._stop.clear()
        self._thread = threading.Thread(name="array generator", target=self.generate)
        self._thread.daemon = True
        self._thread.start()

    def stop_generate(self):
        assert self._thread is not None
        self._stop.set()

        # to avoid blocking when unsubscribe from callback
        # Note: in real life it's better to join() in start_generate()
        if threading.current_thread() != self._thread:
            self._thread.join()
        self._thread = None

    # method for thread
    def generate(self):
        while not self._stop.isSet():
            self.count += 1
            array = self._create_one(self.shape, self.bpp, self.count)
            if len(array):
                array[0][0] = self.count
#            print "generating array %d" % self.count
            self.notify(array)
            time.sleep(0.05) # wait a bit see if the subscribers still want data


class SynchronizableDataFlow(model.DataFlow):
    # very basic dataflow
    def __init__(self, *args, **kwargs):
        model.DataFlow.__init__(self, *args, **kwargs)
        self._thread_must_stop = threading.Event()
        self._thread = None
        self._sync_event = None
        self.max_lat = []
        self._got_event = threading.Event()

    def get_max_lat(self):
        return self.max_lat

    def get_event_type(self):
        """
        return "sw", "hw" or None
        """
        if self._sync_event is None:
            return None
        elif issubclass(self._sync_event.get_type(), model.HwTrigger):
            return "hw"
        else:
            return "sw"

    @oneway
    def onEvent(self, triggert=None):
        if triggert: # sent for debug only
            latency = time.time() - triggert
            self.max_lat.append(latency)
        self._got_event.set()

    def _wait_event_or_stop_cb(self):
        """
        return True if must stop, False otherwise
        """
        while not self._thread_must_stop.is_set():
            event = self._sync_event
            if event is None:
                return False
            # In practice, this would be a hardware signal, not an threading.event!
            if self._got_event.wait(timeout=0.1):
                self._got_event.clear()
                return False
        return True

    def _thread_main(self):
        i = 0
        # generate a stupid array every time we receive an event
        while not self._thread_must_stop.is_set():
            time.sleep(0.01) # bit of "initialisation" time

            must_stop = self._wait_event_or_stop_cb()
            if must_stop:
                break

#            time.sleep(1) #DEBUG: for test over-run
            i += 1
            data = model.DataArray([[i, 0],[0, 0]], metadata={"a": 2, "num": i})
            self.notify(data)
        self._thread_must_stop.clear()

    def start_generate(self):
        # if there is already a thread, wait for it to finish before starting a new one
        if self._thread:
            self._thread.join()
            assert not self._thread_must_stop.is_set()
            self._thread = None

        # create a thread
        self._thread = threading.Thread(target=self._thread_main, name="flow thread")
        self._thread.start()

    def stop_generate(self):
        assert self._thread
        assert not self._thread_must_stop.is_set()
        # we don't wait for the thread to stop fully
        self._thread_must_stop.set()

    def synchronizedOn(self, event):
        if self._sync_event == event:
            return

        if self._sync_event:
            self._sync_event.unsubscribe(self)

        self._sync_event = event
        if self._sync_event:
            self._sync_event.subscribe(self)


if __name__ == "__main__":
    #import sys;sys.argv = ['', 'Test.testName']
    unittest.main()
