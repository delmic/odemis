import http.client
import xml.etree.ElementTree as ElementTree
from xml.etree.ElementTree import SubElement,Element, Comment, tostring

from ConsoleClient.Abstractions.Datamodel import Datamodel
from ConsoleClient.Communication.MessageConnection import MessageConnection
from ConsoleClient.Communication.DataConnection import DataConnection

import ConsoleClient.Communication.ComStrList



class Connection(object):
    """Manages the connection between the python enviroment and the Orsay Physics server""" 
       
    #generalized strings to provide spelling consistency
    Search="SEARCH"
    Post="POST"
    Request="Request"
    RequestType="requestType"
    ConnectionString="Connection"
    NameString="name"
    ConfigurationString="Configuration"
    DeviceString="Device"



    #initialize the connection object
    def __init__(self,Tcp_ip='127.0.0.1',Tcp_port=3500,*args,**kwargs):
        """Creates the connection object, (serverIP,Port) default connects to localHost"""
        #this is where the instance specific values must go
        self.Tcp_ip = Tcp_ip
        self.Tcp_port = Tcp_port
        self.datamodel=Datamodel()       
        self.MessageConnection=None
        self.connected=False
        self.serverTimeout=False
        
        #establish the main connection here
        self.HttpConnection=http.client.HTTPConnection(self.Tcp_ip,self.Tcp_port)
        Connection.LoginToServer(self)        
        return super().__init__(*args, **kwargs)       


    def LoginToServer(self):
        """Establishes and initializes the connection with the server"""
        try:
            #declare ports to be requested
            self.messagePort=int()
            self.dataPort=int()
            self.accesLevel=int()

            #hardcode login untill a config or call function is made
            password = "DangerNoodle"
            login = "Python"                        
            connectionRequestXML=Connection.CreateLoginXML(login,password)           
            #ask server for a new connection
            self.HttpConnection.request(Connection.Post,"/",ElementTree.tostring(connectionRequestXML))
            connectionInfo=self.HttpConnection.getresponse()
            if(connectionInfo.status != 200):
                print("Connection failed: "+
                      connectionInfo.status+
                     connectionInfo.reason)
                pass
            #save the return values 
            self.ParseLoginReply(connectionInfo.read())

            #open messgeport, allow for update messages to be handled
            self.DataConnection=DataConnection(self.dataPort,self.Tcp_ip)
            self.MessageConnection=MessageConnection(self.messagePort,self.Tcp_ip,self.datamodel,self.DataConnection)

            #get the configuration
            self.GetServerConfiguration() #this fills the datamodel with the roots
            self.MessageConnection.InitializeAllDevices() #this fills all the root devices
            
            #open data port            
                         
            
 
        except Exception as ex:
            print ('failed to establish a new connection with the server')
            print (ex)
            raise Exception
            return
        pass   

    
    @staticmethod
    def CreateLoginXML(user:str,password:str)->Element:
        
        #declare the xmlroot with request attribute
        rootnode=ElementTree.Element(Connection.Request)
        rootnode.set(Connection.RequestType,Connection.ConnectionString)
        #add the user&pw info, password info is not secure, securing it is out of scope 
        usernode=ElementTree.Element("User")
        usernode.text=user
        pwnode=ElementTree.Element("Password")
        pwnode.text=password
        rootnode.append(usernode)
        rootnode.append(pwnode)
        return rootnode

    def ParseLoginReply(self,reply:str):
        loginReplyNode=ElementTree.fromstring(reply)
        param=Element('')
        for param in loginReplyNode.iter("Param"):            
            name=param.get(Connection.NameString)
            if(name == 'MessagePort'):
               self.messagePort=int(param.text) ; continue 
            if(name == 'DataPort'):
                self.dataPort=int(param.text);  continue
            if(name == 'AccessLevel'):
                self.accesLevel=int(param.text); continue
        pass

    @staticmethod
    def CreateServerConfigurationXML():
        #declare root with configuration request attribute
        rootnode=Element(Connection.Request)
        rootnode.set(Connection.RequestType,Connection.ConfigurationString)
        return rootnode
        
    def GetServerConfiguration(self):
        """Requires datamodel to have a callback"""
        requestXML=self.CreateServerConfigurationXML()
        self.HttpConnection.request(Connection.Search,"/",ElementTree.tostring(requestXML))
        response=self.HttpConnection.getresponse()
        if(response.status!=200):
            print("Failed to obtain server configuration"
                  +response.status
                  +response.reason)
            pass
        self.ParseServerConfig(response.read())
        pass


    def ParseServerConfig(self,response):
        Confignode=ElementTree.fromstring(response)
        device=Element('')
        for device in Confignode.findall(Connection.DeviceString):           
            self.datamodel.AddDevice(device.text)        
        pass
