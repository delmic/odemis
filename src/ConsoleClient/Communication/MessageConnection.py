
#python imports
import http
from threading import RLock,Thread
import signal
import xml.etree.ElementTree as ElementTree
from xml.etree.ElementTree import SubElement,Element, Comment, tostring
import inspect 
import time
#orsay imports
from ConsoleClient.Abstractions.Device import Device
from ConsoleClient.Abstractions.Parameter import Parameter #needed?
#from ConsoleClient.Abstractions.Datamodel import Datamodel
from ConsoleClient.Abstractions.Datamodel import Datamodel
from ConsoleClient.Communication.ComStrList import * 
from ConsoleClient.Communication.DataConnection import DataConnection

class MessageConnection(object):
    """handles the message port"""          


    def __init__(self,Tcp_port:int,Tcp_ip:str,datamodel:Datamodel,dataConnection:DataConnection,*args, **kwargs):
           
        self.ConnectToPort(Tcp_port,Tcp_ip)
        self.dataConnection=dataConnection #we need it to send tokens

        self.datamodel=datamodel
        self.datamodel.SetCallback(self.RootDeviceCallback) # provide the communcation updates to the datamodel 
        self.datamodel.SetDataCallback(self.DataCallback);
        self.connectionLock=RLock() #we want out httpstatus to correspond to the correct call.
        self.queryTime=0.2 #delay between two queries in seconds
        QueryThread=Thread(target=self.QueryServer)
        QueryThread.daemon = True
        QueryThread.start()
        return super().__init__(*args, **kwargs)
    
    

    def ConnectToPort(self,Tcp_port:int,Tcp_ip:str):
        """established the connection to the messagehandler port"""          
        self.Connection=http.client.HTTPConnection(Tcp_ip,Tcp_port)
        return self.Connection

    def RootDeviceCallback(self,device:Element):
        """Final callback of devices/parameters, sends the xml to the server"""
        
        #slightly unpretty indirect information prevents us from creating 2 callback lines
        #we do not mix paramter updates with action calls to reduce risk of calling actions with unknown parameter states.
        for subElement in device.iter():
            if (subElement.tag =='Parameter'): #if it has parameter nodes then it is a parameter update                
                self.SendXMLRequest(device,SetParametersStr)
                break #only send 1 update message
            if (subElement.tag =='Action'): #if it has  action nodes it is an action call
                self.SendXMLRequest(device,DoActionStr)
                break
    
    def DataCallback(self, device:Element):
        """final callback for datacalling methods"""
        subelement=Element('');
        for subelement in device.iter():
            if (subelement.tag=='Action' and 'DataTokenMethod' in subelement.attrib):
                #ask method through messagehandler
                reply=self.SendXMLRequest(device,GetData)
                xmlReply=ElementTree.fromstring(reply)   #we can make this fancier but for now just get the token
                token= xmlReply.find('DataToken').text
                data = self.dataConnection.SendDataRequest(int(token))
                
                #return bytearray to cqller               
                
                return  data


        pass
    
    def RequestXMLRoot(self,requestType:str):
        rootElement=Element(RequestStr,{RequestTypeStr:requestType})
        return rootElement        

    def GetDeviceNode(self,device:Device):
        deviceElement=Element("Device",{NameStr:device.name})
        return deviceElement      

    def InitializeDevice(self,device:Device):
        #build xml request
        requestNode=self.RequestXMLRoot(GetFullDeviceStr)
        requestNode.append(self.GetDeviceNode(device))
        #send to server
        self.connectionLock.acquire()#start of connection use
        self.Connection.request(GetStr,"/",tostring(requestNode))
        response=self.Connection.getresponse()
        #verify return status and reply structure
        self.ConfirmReply(response)
        xmlReply=ElementTree.fromstring(response.read())
        self.connectionLock.release() #end of connection use
        self.HandleParameterUpdate(xmlReply)
        pass

    def InitializeAllDevices(self):
        #devices=[]
        for attr in dir(self.datamodel):
            attr=getattr(self.datamodel,attr)
            if type(attr)==Device:
                #devices.append(attr) #debug list placeholder
                self.InitializeDevice(attr)        
        pass

    def SendXMLRequest(self,deviceNode:Element,requestType:str):
        requestRoot=self.RequestXMLRoot(requestType)
        requestRoot.append(deviceNode)
        self.connectionLock.acquire()#start connection
        self.Connection.request(PostStr,"/",tostring(requestRoot))        
        response=self.Connection.getresponse()
        returnData=response.read()
        self.ConfirmReply(response)
        self.connectionLock.release()
        return returnData

    def GetUpdates(self):
        requestNode=self.RequestXMLRoot(GetUpdatedStr)
        self.connectionLock.acquire()
        self.Connection.request(GetStr,"/",tostring(requestNode))
        response=self.Connection.getresponse()
        xmlReply=ElementTree.fromstring(response.read())
        self.connectionLock.release()
        self.HandleParameterUpdate(xmlReply)


    def ConfirmReply(self,response):
        """"Verify http status code """
        if(response.status != 200):
            print("Connection failed: "+
                response.status+
                response.reason)
            pass
        pass

    def HandleParameterUpdate(self,message:Element):
        """takes the xml to update the datamodel"""
        #we can do some checking here
        self.datamodel.UpdateModel(message)
        pass

    def QueryServer(self):
        time.sleep(0.5) #small delay before we start asking messages like a mad man 
        delaytimes=[]
        freq=5; #Hz
        pause=1/freq #seconds
        
        while(True):
            
            start=time.time()
            
            self.connectionLock.acquire()
            self.GetUpdates()
            #self.InitializeAllDevices() #for testing
            self.connectionLock.release()     
            
            end=time.time()
            deltaTime=(end-start)

            #delaytimes.append(deltaTime)
            #mean=(sum(delaytimes))/len(delaytimes)
            #print(deltaTime*1000)
            
            timeLeft=pause-deltaTime
            if(timeLeft>0):
                time.sleep(timeLeft)
            pass






