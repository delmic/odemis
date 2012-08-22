#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
Created on 17 Jul 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
import model
import pickle
import threading
import time
import unittest


class SimpleDataFlow(model.DataFlow):
    # very basic dataflow
    def __init__(self, *args, **kwargs):
        model.DataFlow.__init__(self, *args, **kwargs)
        self._thread_must_stop = threading.Event()
        self._thread = None
    
    def _thread_main(self):
        i = 0
        # generate a stupid array every 0.1s
        while not self._thread_must_stop.wait(0.1):
            data = model.DataArray([[i, 0],[0, 0]], metadata={"a": 1, "num": i})
            i += 1
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
        
        
class TestDataFlow(unittest.TestCase):
    def test_dataarray_pickle(self):
        darray = model.DataArray([[1, 2],[3, 4]], metadata={"a": 1})
        jar = pickle.dumps(darray)
        up_darray = pickle.loads(jar)
        self.assertEqual(darray.data, up_darray.data, "data is different after pickling")
        self.assertEqual(darray.metadata, up_darray.metadata, "metadata is different after pickling")
        self.assertEqual(up_darray.metadata["a"], 1)

#    @unittest.skip("not implemented")
    def test_df_subscribe_get(self):
        self.df = SimpleDataFlow()
        self.size = (2,2)
        
        number = 5
        self.left = number
        self.df.subscribe(self.receive_data)
        
        time.sleep(0.2)
        
        # get one image: should be shared with the subscribe
        im = self.df.get()
        
        # get a second image: also shared
        im = self.df.get()

        self.assertEqual(im.shape, self.size)
        self.assertIn("a", im.metadata)
        
        for i in range(number):
            # end early if it's already finished
            if self.left == 0:
                break
            time.sleep(0.2) # 0.2s per image should be more than enough in any case
        
        self.assertEqual(self.left, 0)
    
    def test_df_double_subscribe(self):
        self.df = SimpleDataFlow()
        self.size = (2,2)
        number, number2 = 8, 3
        self.left = number
        self.df.subscribe(self.receive_data)
        
        time.sleep(0.2) # long enough to be after the first data
        self.left2 = number2
        self.df.subscribe(self.receive_data2)
        
        for i in range(number):
            # end early if it's already finished
            if self.left == 0:
                break
            time.sleep(0.2) # 0.2s should be more than enough in any case
        
        self.assertEqual(self.left2, 0) # it should be done before left
        self.assertEqual(self.left, 0)

    def receive_data(self, dataflow, data):
        """
        callback for df
        """
        self.assertEqual(data.shape, self.size)
        self.assertIn("a", data.metadata)
#        print "Received an image"
        self.left -= 1
        if self.left <= 0:
            dataflow.unsubscribe(self.receive_data)


    def receive_data2(self, dataflow, data):
        """
        callback for df 
        """
        self.assertEqual(data.shape, self.size)
        self.assertIn("a", data.metadata)
        self.left2 -= 1
        if self.left2 <= 0:
            dataflow.unsubscribe(self.receive_data2)


if __name__ == "__main__":
    unittest.main()