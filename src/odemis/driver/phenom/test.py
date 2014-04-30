from suds.client import Client
import Image
import base64
import urllib2
import os
phenom = Client("http://localhost:8888?om", location="http://localhost:8888", username="SummitTAD", password="SummitTADSummitTAD")
# password not need when accessing from localhost?
# In case you don't want to download the whole wsdl first:
# wsdlp = os.path.abspath("./WebServiceG3/Phenom.wsdl")
# phenom = Client("file://" + urllib2.quote(wsdlp), location="http://localhost:8888")
status = phenom.service.GetDoorStatus()
print status
imagingDevice = phenom.factory.create('ns0:imagingDevice')
phenom.service.SelectImagingDevice(imagingDevice.SEMIMDEV)  # or NAVCAMIMDEV
result = phenom.service.SEMGetLiveImageCopy(1)
# result = phenom.service.NavCamGetLiveImageCopy(1)
size = result.image.descriptor.width, result.image.descriptor.height
image = Image.frombuffer('L', size, base64.b64decode(result.image.buffer[0]), 'raw', "L", 0, 1)
image.show()
