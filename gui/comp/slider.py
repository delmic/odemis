
import wx
from wx.lib.agw.aui.aui_utilities import StepColour, MakeDisabledBitmap, \
    DarkenBitmap

from gui.img.data import getsliderBitmap, getslider_disBitmap
#from gui.log import log

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



class CustomSlider(wx.PyPanel):
    """
    Custom Slider class
    """

    def __init__(self, parent, id=wx.ID_ANY, value=0.0, val_range=(0.0, 1.0), size=(-1, -1),
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

        self.current_value = value
        self.value_range = val_range

        self.range_span = float(val_range[1] - val_range[0])
        #event.GetX() position or Horizontal position across Panel
        self.x = 0
        #position of pointer
        self.pointerPos = 0

        #Get Pointer's bitmap
        self.bitmap = getsliderBitmap()
        self.bitmap_dis = getslider_disBitmap()

        # Pointer dimensions
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
        return self.value_range[0]

    def GetMax(self):
        return self.value_range[1]

    def OnPaint(self, event=None):
        dc = wx.BufferedPaintDC(self)
        width, height = self.GetSize()
        _, half_height = width / 2, height / 2

        bgc = self.Parent.GetBackgroundColour()
        dc.SetBackground(wx.Brush(bgc, wx.SOLID))
        dc.Clear()

        fgc = self.Parent.GetForegroundColour()

        if not self.Enabled:
            fgc = StepColour(fgc, 50)


        dc.SetPen(wx.Pen(fgc, 1))

        dc.DrawLine(self.half_h_width, half_height,
                    width - self.half_h_width, half_height)


        if self.Enabled:
            dc.DrawBitmap(self.bitmap,
                          self.pointerPos,
                          half_height - self.half_h_height,
                          True)
        else:
            dc.DrawBitmap(self.bitmap_dis,
                          self.pointerPos,
                          half_height - self.half_h_height,
                          True)

        event.Skip()

    def OnLeftDown(self, event=None):
        #Capture Mouse
        # log.debug("OnLeftDown")
        self.CaptureMouse()
        self.getPointerLimitPos(event.GetX())

        self.Refresh()
        event.Skip()


    def OnLeftUp(self, event=None):
        #Release Mouse
        # log.debug("OnLeftUp")
        if self.HasCapture():
            self.ReleaseMouse()
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
        self.current_value = self._pixel_to_val()


    def OnMotion(self, event=None):
        if self.GetCapture():
            self.getPointerLimitPos(event.GetX())
            self.Refresh()


    def OnSize(self, event=None):
        """
        If Panel is getting resize for any reason then calculate pointer's position
        based on it's new size
        """
        self.pointerPos = self._val_to_pixel()
        self.Refresh()

    def _val_to_perc(self):
        """ Give the value as a range percentage """
        return ((self.current_value - self.value_range[0]) / self.range_span) * 100.0

    def _val_to_pixel(self):
        slider_width = self.GetSize()[0] - self.handle_width
        return int(abs(slider_width * (self._val_to_perc() / 100)))

    def _pixel_to_val(self):
        prcnt = float(self.pointerPos) / (self.GetSize()[0] - self.handle_width)
        return int((self.value_range[1] - self.value_range[0]) * prcnt + self.value_range[0])

    def SetValue(self, value):
        if value < self.value_range[0]:
            self.current_value = self.value_range[0]
        elif value > self.value_range[1]:
            self.current_value = self.value_range[1]
        else:
            self.current_value = value

        self.pointerPos = self._val_to_pixel()

        self.Refresh()

    def GetValue(self):
        return self.current_value

    def GetRange(self, range):
        self.value_range = range

