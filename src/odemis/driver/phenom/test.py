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
phenom = Client("http://Phenom-MVE0206151080.local:8888?om", location="http://Phenom-MVE0206151080.local:8888", username="delmic", password="6526AM9688B1")
# phenom = Client("http://localhost:8888?om", location="http://localhost:8888", username="SummitTAD", password="SummitTADSummitTAD")

# password not need when accessing from localhost?
# In case you don't want to download the whole wsdl first:
# wsdlp = os.path.abspath("./WebServiceG3/Phenom.wsdl")
# phenom = Client("file://" + urllib2.quote(wsdlp), location="http://localhost:8888")
# range = phenom.service.SEMGetHighTensionRange()
# volt_range = [-range.max, -range.min]
range = phenom.service.GetStageCenterCalib()
print range
range = phenom.service.GetStageModeAndPosition()
print range
stagePos = phenom.factory.create('ns0:position')
stagePos.x = 0
print stagePos

# imagingDevice = phenom.factory.create('ns0:imagingDevice')
# scanParams = phenom.factory.create('ns0:scanParams')
# detectorMode = phenom.factory.create('ns0:detector')
# navAlgorithm = phenom.factory.create('ns0:navigationAlgorithm')
# pos = phenom.factory.create('ns0:position')
# phenom.service.SelectImagingDevice(imagingDevice.SEMIMDEV)  # or NAVCAMIMDEV
# # scanParams = ((50, 50), 1)
#
#
# # Use all detector segments
# detectorMode = 'SEM-DETECTOR-MODE-ALL'
# navAlgorithm = 'NAVIGATION-RAW'
# scanParams.detector = detectorMode
# # Some resolutions are not allowed e.g. 250 doesnt work, 256 does
# scanParams.resolution.width = 2048
# scanParams.resolution.height = 2048
# scanParams.nrOfFrames = 1
# scanParams.HDR = False
# scanParams.center.x = 0.001
# scanParams.center.y = 0.001
# scanParams.scale = 1
#
# pos.x, pos.y = 0, 0
# phenom.service.SetStageCenterCalib(pos)
# print phenom.service.GetStageModeAndPosition()
# pos.x, pos.y = 0.001, 0.001
# resp = phenom.service.MoveBy(pos, navAlgorithm)
# # print resp
# print phenom.service.GetStageModeAndPosition()
#
# start = time.time()
# result = phenom.service.SEMAcquireImageCopy(scanParams)
# phenom.service.Stop()
# end = time.time() - start
# print end
# # result = phenom.service.NavCamGetLiveImageCopy(1)
# size = result.image.descriptor.width, result.image.descriptor.height
# print size
# start = time.time()
# image = numpy.frombuffer(base64.b64decode(result.image.buffer[0]), dtype="uint8")
# end = time.time() - start
# print end
# image.shape = 2048, 2048
# # image = Image.frombuffer('L', size, base64.b64decode(result.image.buffer[0]), 'raw', "L", 0, 1)
# print type(image)
#
# hdf5.export("phe.h5", model.DataArray(image))
# # image.show()
