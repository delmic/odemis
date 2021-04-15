
import http
from threading import RLock,Thread
from ConsoleClient.Communication.ComStrList import * 


class DataConnection(object):
    """Manages the data/token port with the server"""


    def __init__(self,Tcp_port:int,Tcp_ip:str,*args, **kwargs):
           
        self.ConnectToPort(Tcp_port,Tcp_ip)
        
        self.connectionLock=RLock() #we want out httpstatus to correspond to the correct call.        
        return super().__init__(*args, **kwargs)

    def ConnectToPort(self,Tcp_port:int,Tcp_ip:str):
        """established the connection to the messagehandler port"""          
        self.Connection=http.client.HTTPConnection(Tcp_ip,Tcp_port)
        return self.Connection

    def SendDataRequest(self,dataToken):
        
        if isinstance(dataToken,int):
            dataToken=int.to_bytes(dataToken,4,'little')

        self.connectionLock.acquire()#start connection
        self.Connection.request(PostStr,"/",dataToken)        
        response=self.Connection.getresponse()
        returnData=response.read()
        self.ConfirmReply(response)
        self.connectionLock.release()
        return returnData

    def ConfirmReply(self,response):
        """"Verify http status code """
        if(response.status != 200):
            print("Connection failed: "+
                str(response.status)+ " "+
                response.reason)
            pass
        pass