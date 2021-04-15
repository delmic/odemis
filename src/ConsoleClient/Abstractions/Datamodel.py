import xml.etree.ElementTree as ElementTree
from xml.etree.ElementTree import SubElement,Element, Comment, tostring, ElementTree as ETree
from ConsoleClient.Abstractions.Device import Device

class Datamodel(object):
    """holder object for root devices"""
    def __init__(self,*args, **kwargs):
        
        return super().__init__(*args, **kwargs)
        
    def SetCallback(self,callback):
        #do check here
        self.Callback=callback
        pass
    def SetDataCallback(self,callback):
        self.DataCallback=callback

    def AddDevice(self,name):
        """adds a device to the root of the datamodel"""
        newDevice=Device(name,self.Callback) #we can add the devicetype here
        newDevice.SetDataCallback(self.DataCallback)
        self.__setattr__(name,newDevice)
        return newDevice
        

    def UpdateModel(self,updateMessage:Element):
        """updates the datamodel from an xml update"""
        deviceElement=Element('')
        for deviceElement in updateMessage.findall('Device'):  #get all rootdevices           
            deviceName=deviceElement.attrib['Name']
            #if the device does not yet exist create one
            if hasattr(self,deviceName):
                device=getattr(self,deviceName)
            else:
                device=self.AddDevice(devicename)
            #pass update subnode to device
            device.HandleUpdate(deviceElement)
            pass
        pass

        
    



   