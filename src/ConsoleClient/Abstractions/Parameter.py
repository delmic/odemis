import xml.etree.ElementTree as ElementTree
from xml.etree.ElementTree import SubElement,Element, Comment, tostring
import inspect
import weakref
import types


class Parameter(object):
    """Any server parameter, due to the dynamic typing the properties are simply added at runtime to handle any type of parameter"""    

    def __init__(self, name:str,callback):
        """the name is not optional in contrast to the other attributes as it is needed to identify the parameter on the server side"""
        self.Name=name
        self.inspectCallback(callback)
        self.callbacks=set()
        pass    
           
    def inspectCallback(self,callback):
        """Verify if the callback signature is correct and if so add the callback the the class instance"""
        try:
            signature= inspect.signature(callback)
            paramTypes=list(signature.parameters.values())
            nbrParam= len(paramTypes)
            firsttype= paramTypes[0].annotation            
            TypeOK= firsttype is Element
            if TypeOK and nbrParam==1:
                self.updateCallback=callback
            else:
                raise Exception('Invalid callback for a parameterupdate')
        except Exception as ex:
            print(ex)
            raise ex 
        pass
        
    def HandleUpdate(self,updateMessage:Element):

        propUpdate=Element('')      


        for propUpdate in list(updateMessage):
            propName=propUpdate.tag
            propValue=propUpdate.text
            self.__setattr__(propName,propValue,False)


    def __setattr__(self ,name, value ,doUpdate:bool=True):
        """Override of the set attribute function, of the attribute is not being set by the server update should be true in order to send it to the server, if the parameter does not exist then the server will prompt an error"""
                
        if doUpdate and not name=='Name' and hasattr(self,name): #the name and device should never be updated, and the attribute must exist to be update (no update on creation)   
            self.OnAttributeChanged(name,value)         
                        
        r=super().__setattr__(name, value)
        self.notify(name)
        return r

    def Subscribe(self, callback):
        """Adds a callback to be called when the parameter changes"""
        try:
            assert callable(callback)
            # Check the callback signature
            signature=inspect.signature(callback)
            params=list(signature.parameters.values())
            nbrParams=len(params)
            if nbrParams!=2:
                print('Callback must have exactly two parameters, callback %s has %i',callback,nbrParams)
                return

            self.callbacks.add(callback)
        except Exception as ex:
            print('Cannot subscribe callback %s',callback)
            print(ex)
		
    def Unsubscribe(self, callback):
        """Removes a callback"""
        self.callbacks.discard(callback)
        pass

    def notify(self,attributeName):
        """Sends an update that the parameter has changed"""
        if not hasattr(self,'callbacks'):
            return
        for c in self.callbacks.copy():
            try:
                c(self,attributeName)
            except Exception as ex:
                print('Cannot call callback %s for parameter %s',c,self.Name)
                print(ex)

    def OnAttributeChanged(self,attributeName,value):
        """executes when the parameter is changed by the anything but the server(to prevent an update loop) """       
        paramElement=Element('Parameter',{'Name':self.Name})
        subEl= SubElement(paramElement,attributeName)
        subEl.text=str(value)
        self.updateCallback(paramElement)

        pass #we need an update server function