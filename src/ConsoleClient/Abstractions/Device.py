import xml.etree.ElementTree as ElementTree
from xml.etree.ElementTree import SubElement,Element, Comment, tostring
import weakref
import  inspect
from types import MethodType
from ConsoleClient.Abstractions.Parameter import Parameter

class Device(object):
    """Device as installed in the microscope"""    
    
    nameAttr='name'

    def __init__(self,name:str,updateCallback,deviceType:str="abstractDevice"):
        """Creates device object in the client side, name and devicetype are mandatory for identification purposes ,contains parameters"""        
        self.name=name
        self.deviceType=deviceType        
        self.inspectCallback(updateCallback)    
        
        self.needUpdate=False        
        pass

    def inspectCallback(self,callback):
        try:
            signature= inspect.signature(callback)
            paramTypes=list(signature.parameters.values())
            nbrParam= len(paramTypes)
            firsttype= paramTypes[0].annotation
            bool= firsttype == Element
            if bool and nbrParam==1:
                self.updateCallback=callback
            else:
                raise Exception('Invalid callback for a deviceupdate')
        except Exception as ex:
            print(ex)
            raise ex   
        
        pass
    def SetDataCallback(self,callback):
        self.DataCallback=callback
        pass


    def HandleUpdate(self,updateMessage:Element):
        """takes an Xelement and forwards the update to parameters and subdevices"""
        #parameters        
        ParamElement=Element('')
        for ParamElement in updateMessage.findall('Parameter'):#get all parameters of this device
            self.UpdateParam(ParamElement)
            pass
        #actions, initialization only
        actionElement=Element('')
        for actionElement in updateMessage.findall('Action'):
            if 'DataTokenMethod' in actionElement.attrib:
                self.AddDataAction(actionElement)
            else:
                self.AddAction(actionElement)
            pass

        #subdevices
        deviceElement=Element('')
        for deviceElement in updateMessage.findall('Device'): #get all subdevices             
            self.UpdateSubDevice(deviceElement)
            pass
        pass

    def UpdateSubDevice(self,deviceElement:Element):

        deviceName=deviceElement.attrib['Name']
        #if the device does not yet exist create one
        if hasattr(self,deviceName):
            device=getattr(self,deviceName)
        else:
            device=self.AddSubDevice(deviceName)
        #pass update subnode to subdevice
        device.HandleUpdate(deviceElement)
        pass

    def UpdateParam(self,ParamElement:Element):
        paramName=ParamElement.attrib['Name']
        #if the parameter does not exist create one
        if hasattr(self,paramName):
            param=getattr(self,paramName)
        else:
            param=self.AddParameter(paramName)
        param.HandleUpdate(ParamElement)
        pass

    def AddAction(self,actionElement:Element):
        actionName=actionElement.attrib[Device.nameAttr]
        #if the parameter does not exist create one
        if hasattr(self,actionName):
            return #no need if it exists
        def doAction(self): #create a simple callback
            actionElement=Element('Action',{Device.nameAttr:actionName})
            deviceNode=Element('Device',{'Name':self.name})
            deviceNode.append(actionElement)
            self.updateCallback(deviceNode)     
        self.__setattr__(actionName,MethodType(doAction,self)) #add as method to the device
        pass

    def AddDataAction(self,dataActionEl:Element):
        actionName=dataActionEl.attrib[Device.nameAttr]
        


        dataDirection=dataActionEl.attrib['DataTokenMethod']
        #if the parameter does not exist create one
        if hasattr(self,actionName):
            return #no need if it exists
        def doAction(self): #create a simple callback
            actionElement=Element('Action',{Device.nameAttr:actionName,'DataTokenMethod':dataDirection})
            deviceNode=Element('Device',{Device.nameAttr:self.name})
            deviceNode.append(actionElement)
            return self.DataCallback(deviceNode)     
        self.__setattr__(actionName,MethodType(doAction,self)) #add as method to the device
        
        pass

    def ParamCallback(self,param:Element):
        #create device node and append element
        deviceNode=Element('Device',{'Name':self.name})
        deviceNode.append(param)
        self.updateCallback(deviceNode)


    def AddParameter(self,name:str):
        """Creates a new parameter and appends it as an attribute to self"""
        newparam=Parameter(name,self.ParamCallback)
        self.__setattr__(name,newparam)
        return newparam


    def AddSubDevice(self,name:str,devicetype:str='AbstractDevice'):
        """adds a new device to the current one"""
        #specific callback for nested devices
        def nestedCallback(device:Element):            
            upperDevice=Element('Device',{'Name':self.name})
            upperDevice.append(device)
            self.updateCallback(upperDevice)
            pass
        #by adding the subdevice as an attribute we can autocomplete the object live
        newDevice=Device(name,nestedCallback)       
        self.__setattr__(name,newDevice)
        return newDevice

    

        
        

    

        