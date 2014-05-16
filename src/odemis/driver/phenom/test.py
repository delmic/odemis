from suds.client import Client
import Image
import base64
import urllib2
import os
import time
import math
import numpy
from odemis.dataio import hdf5
from odemis import model, util
# phenom = Client("http://10.42.0.53:8888?om", location="http://10.42.0.53:8888", username="SummitTAD", password="SummitTADSummitTAD")
phenom = Client("http://Phenom-MVE0206151080.local:8888?om", location="http://Phenom-MVE0206151080.local:8888", username="delmic", password="6526AM9688B1", timeout=1000)
# phenom = Client("http://localhost:8888?om", location="http://localhost:8888", username="SummitTAD", password="SummitTADSummitTAD")

navAlgorithm = phenom.factory.create('ns0:navigationAlgorithm')
navAlgorithm = 'NAVIGATION-AUTO'

stagePos = phenom.factory.create('ns0:position')

imagingDevice = phenom.factory.create('ns0:imagingDevice')
scanParams = phenom.factory.create('ns0:scanParams')
detectorMode = phenom.factory.create('ns0:detector')
# phenom.service.SelectImagingDevice(imagingDevice.SEMIMDEV)  # or NAVCAMIMDEV

range = phenom.service.GetSEMWD()
# 0.00347684817228
print range
# # # Use all detector segments
# detectorMode = 'SEM-DETECTOR-MODE-ALL'
# scanParams.detector = detectorMode
# # Some resolutions are not allowed e.g. 250 doesnt work, 256 does
# scanParams.resolution.width = 2048
# scanParams.resolution.height = 2048
# scanParams.nrOfFrames = 255
# scanParams.HDR = True
# scanParams.center.x = 0
# scanParams.center.y = 0
# scanParams.scale = 1
#
# start = time.time()
# img_str = phenom.service.SEMAcquireImageCopy(scanParams)
# end = time.time() - start
# print end
# sem_img = numpy.frombuffer(base64.b64decode(img_str.image.buffer[0]), dtype="uint16")
# sem_img.shape = (2048, 2048)
#
# hdf5.export("phe.h5", model.DataArray(sem_img))

