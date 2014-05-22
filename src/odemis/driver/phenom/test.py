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

# phenom.service.SelectImagingDevice(imagingDevice.NAVCAMIMDEV)  # or NAVCAMIMDEV
# status = phenom.service.GetDoorStatus()
# area = phenom.service.GetProgressAreaSelection().target
# print area
# phenom.service.SetSEMWD(0.008723)
# phenom.service.NavCamAbortImageAcquisition()

# print phenom.service.GetInstrumentMode()
# print phenom.service.GetOperationalMode()
# # print oper
# print phenom.service.GetProgressAreaSelection()
# open = phenom.service.GetDoorStatus()
# resp = phenom.service.SelectImagingDevice(imagingDevice.SEMIMDEV)
# resp = phenom.service.UnloadSample()
# print instr, oper, area, open, resp
# print phenom.service.GetSampleHolder().holderType
# # 0.00751932078719

# camParams = phenom.factory.create('ns0:camParams')
# camParams.height = 1
# camParams.width = 1
# start = time.time()
# img_str = phenom.service.NavCamAcquireImageCopy(camParams)
# end = time.time() - start
# print end


# print img_str.image.descriptor
# sem_img = numpy.frombuffer(base64.b64decode(img_str.image.buffer[0]), dtype="uint8")
# print sem_img.shape
# sem_img.shape = (img_str.image.descriptor.height, img_str.image.descriptor.width, 3)
# hdf5.export("navcam.h5", model.DataArray(sem_img))
# print (img_str.aAcqState.pixelHeight, img_str.aAcqState.pixelWidth)
# print (img_str.aAcqState.position.x, img_str.aAcqState.position.y)
# The improved NavCam in Phenom G2 and onwards delivers images with a native
# resolution of 912x912 pixels. When requesting a different size, the image is
# scaled by the Phenom to the requested resolutio
# 0.00347684817228

# """""""""""""""""""""""""""""""""""""""""""""
# # # Use all detector segments
scanMode = 'SEM-SCAN-MODE-IMAGING'
detectorMode = 'SEM-DETECTOR-MODE-ALL'
scanParams.detector = detectorMode
# Some resolutions are not allowed e.g. 250 doesnt work, 256 does
scanParams.resolution.width = 256
scanParams.resolution.height = 256
scanParams.nrOfFrames = 1
scanParams.HDR = True
scanParams.center.x = 0
scanParams.center.y = 0
scanParams.scale = 1
#
phenom.service.SetSEMViewingMode(scanParams, scanMode)
# """""""""""""""""""""""""""""""""""""""""""""
print phenom.service.GetProgressSEMDeviceMode()
# start = time.time()
# img_str = phenom.service.SEMAcquireImage(scanParams)
# phenom.service.SEMAbortImageAcquisition()
# end = time.time() - start
# print end
# # sem_img = numpy.frombuffer(base64.b64decode(img_str.image.buffer[0]), dtype="uint16")
# # sem_img.shape = (256, 256)
#
# mode = phenom.service.GetSEMViewingMode()
# print mode
# hdf5.export("phe.h5", model.DataArray(sem_img))

