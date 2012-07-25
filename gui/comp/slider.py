
import wx
from odemis.gui.img.data import getsliderBitmap
from odemis.gui.log import log

class Slider(wx.Slider):
    """ This custom Slider class was implemented so it would not capture
    mouse wheel events, which were causing problems when the user wanted
    to scroll through the main fold panel bar.
    """

    def __init__(self, *args, **kwargs):
        wx.Slider.__init__(self, *args, **kwargs)
        self.Bind(wx.EVT_MOUSEWHEEL, self.pass_to_scollwin)

    def pass_to_scollwin(self, evt):
        """ This event handler prevents anything from happening to the Slider on
        MOUSEWHEEL events and passes the event on to any parent ScrolledWindow
        """

        # Find the parent ScolledWindow
        win = self.Parent
        while win and not isinstance(win, wx.ScrolledWindow):
            win = win.Parent

        # If a ScrolledWindow was found, pass on the event
        if win:
            win.GetEventHandler().ProcessEvent(evt)

#from wx.lib.embeddedimage import PyEmbeddedImage

# pointer = PyEmbeddedImage(
#     "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAABHNCSVQICAgIfAhkiAAAAl5J"
#     "REFUOI11k0tIlFEUx3/n3O8rRSFHw7GoSIsQgsoMrYZaiCsLiRZlCykSgvatgsBF1KZF0SIM"
#     "pEKJ2kREtJKejgUi5aoHYovsYYlpaTY63z0txnGmiQ4c7r3n+f8f7hEKpG51sKt1W9BevyZI"
#     "lJdolQITs/7L0Fg6ee9Vuuflp/Tz/HjJu5d0thRdOFy//IRTlIrN2NomcCGMPUHGh4g8/vZQ"
#     "6mrng9+ngNn8AiXXjxbfTdSEzQL4FRuwhjNYrBZEkR/v0cFzyMQwBiRHF/qO3Zg7AMw6gLOt"
#     "RZf3bQkPiQKxjfg9F8EtQ1JTyPw0qMOvb0F/juB+fWBdhdZUlmrFw7fp+7J1TdBw52TxC1XE"
#     "iuNEm45CUQVmUQFLQaIUbqQXmX6H92IHr8ztDI40akcYejHASquw1Ff4/blwthnRECkuQ2dA"
#     "nUlbo3YEDdWaUJfxez+FzLwGH/2ngEMWvqGBAdBYrYmgspy4CzMGTY1BNI2pZIBbjoHZYpH0"
#     "LBIYYlAZIx6oGuqy3nngO0SSo+8LUEgEi/HqjGByjvHKmK3MNLO/MrJlhX/FgMk5xnVwNJ1U"
#     "Z7ggg0SdoUFGncto9q1Bzu+cMTiaTmpvf9QtzswFhgsNDfOSw0VdbJCNcc4QZ9bbH3W7z1N8"
#     "XFXOqvoNskMcqIAqiII4EMk7NafXHvur1x5Z19JXvn9a7zZt0eZ8jlIwh6zt4bDv23/e574y"
#     "sHDzmd2Jx6y8bqNsd4pkUajmEJngu/t8V/slO07BMi3J7lp2te2V9t21koiXUQUwPsWXgTeW"
#     "vPXUegbe8Nc6/wGnO+2owwBT9AAAAABJRU5ErkJggg==")


class CustomSlider(wx.PyPanel):
    """
    Custom Slider class
    """

    def __init__(self, parent, id=wx.ID_ANY, value=0.0, range=(0.0, 1.0), size=(-1, -1),
                    pos=wx.DefaultPosition, style=wx.NO_BORDER, name="CustomSlider"):

        """
        Default class constructor.
        @param parent: Parent window. Must not be None.
        @param id: CustomSlider identifier. A value of -1 indicates a default value.
        @param pos: CustomSlider position. If the position (-1, -1) is specified
                    then a default position is chosen.
        @param size: CustomSlider size. If the default size (-1, -1) is specified
                     then a default size is chosen.
        @param style: use wx.Panel styles
        @param name: Window name.
        """

        wx.PyPanel.__init__(self, parent, id, pos, size, style, name)

        self.value = value
        self.range = range

        self.difference = range[1]-range[0]
        #event.GetX() position or Horizontal position across Panel
        self.x = 0
        #position of pointer
        self.pointerPos = 0

        #Get Pointer's bitmap
        self.bitmap = getsliderBitmap()

        self.handle_width, self.handle_height = self.bitmap.GetSize()
        self.half_h_width = self.handle_width / 2
        self.half_h_height = self.handle_height / 2

        #Events
        self.Bind(wx.EVT_PAINT, self.OnPaint)
        self.Bind(wx.EVT_MOTION, self.OnMotion)
        self.Bind(wx.EVT_LEFT_DOWN, self.OnLeftDown)
        self.Bind(wx.EVT_LEFT_UP, self.OnLeftUp)
        self.Bind(wx.EVT_SIZE, self.OnSize)

    def GetMin(self):
        return self.range[0]

    def GetMax(self):
        return self.range[0]

    def OnPaint(self, event=None):
        dc = wx.BufferedPaintDC(self)
        width, height = self.GetSize()
        _, half_height = width / 2, height / 2

        bgc = self.Parent.GetBackgroundColour()
        dc.SetBackground(wx.Brush(bgc, wx.SOLID))
        dc.Clear()
        dc.SetPen(wx.Pen(self.Parent.GetForegroundColour(), 1))

        dc.DrawLine(self.half_h_width, half_height,
                    width - self.half_h_width, half_height)


        dc.DrawBitmap(self.bitmap,
                      self.pointerPos,
                      half_height - self.half_h_height,
                      True)

        event.Skip()

    def OnLeftDown(self, event=None):
        #Capture Mouse
        log.debug("OnLeftDown")
        self.CaptureMouse()
        self.getPointerLimitPos(event.GetX())

        self.Refresh()
        event.Skip()


    def OnLeftUp(self, event=None):
        #Release Mouse
        log.debug("OnLeftUp")
        if self.HasCapture():
            self.ReleaseMouse()
        log.debug(self.value)
        event.Skip()


    def getPointerLimitPos(self, xPos):
        #limit movement if X position is greater then self.width
        if xPos > self.GetSize()[0] - self.half_h_width:
            self.pointerPos = self.GetSize()[0] - self.handle_width
        #limit movement if X position is less then 0
        elif xPos < self.half_h_width:
            self.pointerPos = 0
        #if X position is between 0-self.width
        else:
            self.pointerPos = xPos - self.half_h_width

        #calculate value, based on pointer position
        prcnt = float(self.pointerPos)/(self.GetSize()[0]-16)
        self.value =  int((self.range[1]-self.range[0])*prcnt+self.range[0]        )


    def OnMotion(self, event=None):
        if self.GetCapture():
            self.getPointerLimitPos(event.GetX())
        self.Refresh()


    def OnSize(self, event=None):
        """
        If Panel is getting resize for any reason then calculate pointer's position
        based on it's new size
        """
        #Get the size of Panel
        size = self.GetSize()[0]-16 #16=pointer's width

        #Based on panel size and "range" difference calculate pointer position on "panel"
        prcnt = ((self.range[0]-self.value)/self.difference)*100.0
        self.pointerPos = int(abs(size*(prcnt/100)))
        self.Refresh()

    def SetValue(self, value):
        self.value = value

    def GetValue(self):
        return self.value

    def GetRange(self, range):
        self.range = range

