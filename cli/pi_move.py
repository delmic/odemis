'''
Created on 23 Apr 2012

@author: piel
'''
from driver import pi
import time

CONFIG_RS_SECOM_2 = {'x': (1, 0), 'y': (0, 0)}
PORT = "/dev/ttyUSB1"
if __name__ == '__main__':
    stage = pi.StageRedStone("test", "stage", None, PORT, CONFIG_RS_SECOM_2)
#    move = {'x':0.01e-6, 'y':0.01e-6}
    move = { 'y':-.8e-6}
    
    stage.speed.value = {"x":1, "y":1}
    
    start = time.time()
    stage.moveRel(move)
    stage.waitStop(move.keys()[0])
    duration = time.time() - start
    for axis, distance in move.items(): 
        speed = distance/duration
        print "Axis %s, duration = %f,  speed = %fm/s" % (axis, duration, speed)
        