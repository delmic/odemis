'''
Created on 20 Jul 2012

@author: piel
'''
import math
import numpy
import wx

class VideoDisplayer(object):
    '''
    Very simple display for a continuous flow of images as a window
    It should be pretty much platform independent.
    '''


    def __init__(self, title="Live image", size=(640,480)):
        '''
        Displays the window on the screen
        size (2-tuple int,int): X and Y size of the window at initialisation
        Note that the size of the window automatically adapts afterwards to the
        coming pictures
        '''
        self.app = ImageWindowApp(title, size)
    
    def new_image(self, data):
        """
        Update the window with the new image (the window is resize to have the image
        at ratio 1:1)
        data (numpy.ndarray): an 2D array containing the image (can be 3D if in RGB)
        """
        # Can be called from a separate thread, so don't call directly wxPython (not even BitmapFromImage)
        size = data.shape[0:2]
        # adapt the brightness (and make sure the image fits 8 bits)
#        maxval = numpy.amax(data)
#        if maxval < 256:
#            # too dark
#            scale = int(math.floor(256.0 / maxval))
#            drescaled = data * scale
#        else:
#            scale = int(math.ceil(maxval / 256.0))
#            drescaled = data / scale
        minmax = [numpy.amin(data), numpy.amax(data)]
        drescaled = numpy.interp(data, minmax, [0, 256])
            
        data8 = numpy.array(drescaled, dtype="uint8") # 1 copy
        rgb = numpy.dstack((data8, data8, data8)) # 1 copy
        self.app.img = wx.ImageFromData(*size, data=rgb.tostring())
        wx.CallAfter(self.app.update_view)
    
    def waitQuit(self):
        """
        returns when the window is closed (or the user pressed Q)
        """
        self.app.MainLoop() # TODO we could use a Event if multiple accesses must be supported
    
    
class ImageWindowApp(wx.App):
    def __init__(self, title="Image", size=(640,480)):
        wx.App.__init__(self, redirect=False)
        self.AppName = "Odemis CLI"
        self.frame = wx.Frame(None, title=title, size=size)
 
        self.frame.Bind(wx.EVT_KEY_DOWN, self.OnKey)
        self.frame.Bind(wx.EVT_KEY_UP, self.OnKey) # EVT_CHAR and EVT_KEY_DOWN seems to not work in Ubuntu
        self.panel = wx.Panel(self.frame)
        
        self.img = wx.EmptyImage(*size, clear=True)
        self.imageCtrl = wx.StaticBitmap(self.panel, wx.ID_ANY, wx.BitmapFromImage(self.img))
 
#        self.mainSizer = wx.BoxSizer(wx.VERTICAL)
#        self.mainSizer.Add(self.imageCtrl, 0, wx.ALL, 5)
#        self.panel.SetSizer(self.mainSizer)
#        self.mainSizer.Fit(self.frame)
 
        self.panel.Layout()
        self.panel.SetFocus()
        self.frame.Show()
    
    def update_view(self):
        self.frame.Size = self.img.GetSize()
        self.imageCtrl.SetBitmap(wx.BitmapFromImage(self.img))
    
    def OnKey(self, event):
        key = event.GetKeyCode()
        if key in [ord("q"), ord("Q")]:
            self.frame.Destroy()
            
        # everything else we don't process
        event.Skip()