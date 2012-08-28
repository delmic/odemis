#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
Created on 28 Aug 2012

@author: piel
'''
from odemis.cli import main

def print_speed(speed):
    print speed

def print_res(res):
    print "%d x %d" % (res[0], res[1])
    
if __name__ == '__main__':
    comp = main.get_component("FakePIGCS")
    comp.speed.subscribe(print_speed, init=True)
    cam = main.get_component("Andor SimCam")
    cam.targetTemperature.subscribe(print_speed, init=True)
    raw_input("Press Enter to end...")
