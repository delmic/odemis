from suds.client import Client
import Image
import base64
import urllib2
import os
import time
# phenom = Client("http://10.42.0.53:8888?om", location="http://10.42.0.53:8888", username="SummitTAD", password="SummitTADSummitTAD")
# phenom = Client("http://Phenom-MVE0206151080.local:8888?om", location="http://Phenom-MVE0206151080.local:8888", username="delmic", password="6526AM9688B1")
phenom = Client("http://localhost:8888?om", location="http://localhost:8888", username="SummitTAD", password="SummitTADSummitTAD")

# password not need when accessing from localhost?
# In case you don't want to download the whole wsdl first:
# wsdlp = os.path.abspath("./WebServiceG3/Phenom.wsdl")
# phenom = Client("file://" + urllib2.quote(wsdlp), location="http://localhost:8888")
status = phenom.service.GetDoorStatus()
print status
range = phenom.service.SEMGetHighTensionRange()
volt_range = [-range.max, -range.min]
range = phenom.service.GetSEMHFWRange()
phenom.service.SEMSetSpotSize(5)
phenom.service.SEMSetHighTension(-8000)
print phenom.service.SEMGetHighTension()

imagingDevice = phenom.factory.create('ns0:imagingDevice')
scanParams = phenom.factory.create('ns0:scanParams')
detectorMode = phenom.factory.create('ns0:detector')
navAlgorithm = phenom.factory.create('ns0:navigationAlgorithm')
phenom.service.SelectImagingDevice(imagingDevice.SEMIMDEV)  # or NAVCAMIMDEV
# scanParams = ((50, 50), 1)
detectorMode = 'SEM-DETECTOR-MODE-ALL'
nalg = 'NAVIGATION-RAW'
scanParams.detector = detectorMode
# Some resolutions are not allowed e.g. 250 doesnt work, 256 does
scanParams.resolution.width = 512
scanParams.resolution.height = 512
scanParams.nrOfFrames = 2
scanParams.HDR = False
scanParams.center.x = 0
scanParams.center.y = 0
scanParams.scale = 1

resp = phenom.service.MoveBy((0.001, 0.001), nalg)
# print resp
print phenom.service.GetStageModeAndPosition()
# print scanParams
start = time.time()
result = phenom.service.SEMAcquireImageCopy(scanParams)
end = time.time() - start
# print result
print end
# result = phenom.service.NavCamGetLiveImageCopy(1)
size = result.image.descriptor.width, result.image.descriptor.height
image = Image.frombuffer('L', size, base64.b64decode(result.image.buffer[0]), 'raw', "L", 0, 1)
# image.show()
