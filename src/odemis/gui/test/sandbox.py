# -*- coding: utf-8 -*-
from __future__ import division
import array
import random
import numpy

import wx
from wx.lib.delayedresult import startWorker



#=============================================================================

# THREAD RENDER TEST
from odemis.gui.win.delphi import CalibrationProgressDialog
from odemis.model import DataArray


class DrawPanelDBT(wx.Panel):
    """
    Complex panel with its content drawn in another thread
    """
    def __init__(self, *args, **kwargs):
        wx.Panel.__init__(self, *args, **kwargs)

        self.t = None
        self.w, self.h = wx.Window.GetClientSize(self)
        self.buffer = wx.EmptyBitmap(self.w, self.h)

        self.Bind(wx.EVT_PAINT, self.OnPaint)
        self.Bind(wx.EVT_SIZE, self.OnSize)
        self.Bind(wx.EVT_ERASE_BACKGROUND, self.OnEraseBackground)

        self.timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.OnTimer, self.timer)

        self.SizeUpdate()

    #-------------------------------------------------------------------------
    def OnPaint(self, event):
        # Just draw prepared bitmap
        wx.BufferedPaintDC(self, self.buffer)

    #-------------------------------------------------------------------------
    def OnSize(self, event):
        self.w, self.h = wx.Window.GetClientSize(self)
        self.buffer = wx.Bitmap(self.w, self.h)
        self.Refresh()
        self.Update()
        # After drawing empty bitmap start update
        self.SizeUpdate()

    #-------------------------------------------------------------------------
    def OnEraseBackground(self, event):
        pass # Or None

    #-------------------------------------------------------------------------
    def OnTimer(self, event):
        # Start another thread which will update the bitmap
        # But only if another is not still running!
        if self.t is None:
            self.timer.Stop()
            self.t = startWorker(self.ComputationDone, self.Compute)

    #-------------------------------------------------------------------------
    def SizeUpdate(self):
        # The timer is used to wait for last thread to finish
        self.timer.Stop()
        self.timer.Start(100)

    #-------------------------------------------------------------------------
    def Compute(self):
        # Compute Fractal
        MI = 20

        def mapme(x, minimal, maximal, newmin, newmax):
            return(((float(x) - minimal) / (maximal - minimal))
                   * (newmax - newmin) + newmin)

        def compute(x, y):
            z = complex(0, 0)
            c = complex(x, y)
            for i in range(MI):
                z = z**2 + c
                if abs(z) > 2:
                    return i+1
            return 0

        def color(i):
            a = int(mapme(i, 1, MI, 0, 255))
            return(a, a, a)

        def compute_buff(x1, x2, y1, y2, w, h):
            buffer = array.array('B')
            for y in range(h):
                for x in range(w):
                    i = compute(mapme(x, 0, w, x1, x2),
                                mapme(y, 0, h, y2, y1))
                    if i == 0:
                        buffer.extend((255, 255, 255))
                    else:
                        buffer.extend(color(i))
            return buffer

        width, height = self.w, self.h
        x = -0.5
        y =  0.0
        w =  2.4
        h = w * height / width
        data = compute_buff(x - w/2, x + w/2, y - h/2, y + h/2, width, height)
        temp_buffer = wx.BitmapFromBuffer(width, height, data)
        return temp_buffer

    #-------------------------------------------------------------------------
    def ComputationDone(self, r):
        # When done, take bitmap and place it to the drawing buffer
        # Invalidate panel, so it is redrawn
        # But not if the later thread is waiting!
        temp = r.get()
        if not self.timer.IsRunning():
            self.buffer = temp
            self.Refresh()
            self.Update()
        self.t = None

def generate_img_data(width, height, depth, alpha=255):
    """ Create an image of the given dimensions """

    shape = (height, width, depth)
    rgb = numpy.empty(shape, dtype=numpy.uint8)

    if width > 100 or height > 100:
        tl = random_color(alpha=alpha)
        tr = random_color(alpha=alpha)
        bl = random_color(alpha=alpha)
        br = random_color(alpha=alpha)

        rgb = numpy.zeros(shape, dtype=numpy.uint8)

        rgb[..., -1, 0] = numpy.linspace(tr[0], br[0], height)
        rgb[..., -1, 1] = numpy.linspace(tr[1], br[1], height)
        rgb[..., -1, 2] = numpy.linspace(tr[2], br[2], height)

        rgb[..., 0, 0] = numpy.linspace(tl[0], bl[0], height)
        rgb[..., 0, 1] = numpy.linspace(tl[1], bl[1], height)
        rgb[..., 0, 2] = numpy.linspace(tl[2], bl[2], height)

        for i in xrange(height):
            sr, sg, sb = rgb[i, 0, :3]
            er, eg, eb = rgb[i, -1, :3]

            rgb[i, :, 0] = numpy.linspace(int(sr), int(er), width)
            rgb[i, :, 1] = numpy.linspace(int(sg), int(eg), width)
            rgb[i, :, 2] = numpy.linspace(int(sb), int(eb), width)

        if depth == 4:
            rgb[..., 3] = min(255, max(alpha, 0))

    else:
        for w in xrange(width):
            for h in xrange(height):
                rgb[h, w] = random_color((230, 230, 255), alpha)

    return DataArray(rgb)

def random_color(mix_color=None, alpha=255):
    """ Generate a random color, possibly tinted using mix_color """
    red = random.randint(0, 255)
    green = random.randint(0, 255)
    blue = random.randint(0, 255)

    if mix_color:
        red = (red - mix_color[0]) / 2
        green = (green - mix_color[1]) / 2
        blue = (blue - mix_color[2]) / 2

    a = alpha / 255.0

    return red * a, green * a, blue * a, alpha

#=============================================================================

class MainWindow(wx.Frame):
    def __init__(self, *args, **kwargs):
        wx.Frame.__init__(self, *args, **kwargs)
        self.panel = wx.Panel(self)
        self.drawingDBT = DrawPanelDBT(self.panel, size=(300, 300))
        self.sizerPanel = wx.BoxSizer()
        self.sizerPanel.Add(self.panel, proportion=1, flag=wx.EXPAND)
        self.sizerMain = wx.BoxSizer()
        self.sizerMain.Add(self.drawingDBT, 1, wx.ALL | wx.EXPAND, 5)
        self.panel.SetSizerAndFit(self.sizerMain)
        self.SetSizerAndFit(self.sizerPanel)
        self.Show()

        # cpd = CalibrationProgressDialog(self, 1, 1)
        # cpd.ShowModal()

# app = wx.App(False)
# win = MainWindow(None)
# app.MainLoop()

# END THREAD RENDER TEST

#=============================================================================

try:
    import wx.lib.wxcairo as wxcairo
    import cairo
    from odemis.gui import img
    haveCairo = True
except ImportError:
    haveCairo = False


class MyFrame(wx.Frame):
    def __init__(self, parent, title):
        wx.Frame.__init__(self, parent, title=title, size=(640,480))
        self.canvas = CairoPanel(self)
        self.Show()


class CairoPanel(wx.Panel):
    def __init__(self, parent):
        wx.Panel.__init__(self, parent, style=wx.BORDER_SIMPLE)
        self.Bind(wx.EVT_PAINT, self.OnPaint)
        self.text = 'Hello World!'
        self.imgdata = img.getBitmap("canvasbg.png")
        self.offset = 5

    def OnPaint(self, evt):
        #Here we do some magic WX stuff.
        dcp = wx.PaintDC(self)
        width, height = self.GetClientSize()

        dcb = wx.MemoryDC()
        buff = wx.Bitmap(2 * width, 2 * height)
        dcb.SelectObject(buff)

        cr = wx.lib.wxcairo.ContextFromDC(dcb)

        surface = wxcairo.ImageSurfaceFromBitmap(self.imgdata)
        surface.set_device_offset(self.offset, self.offset)

        pattern = cairo.SurfacePattern(surface)
        pattern.set_extend(cairo.EXTEND_REPEAT)
        cr.set_source(pattern)

        cr.rectangle(0, 0, width, height)
        cr.fill()

        #Here's actual Cairo drawing
        size = min(width, height)
        cr.scale(size, size)
        cr.set_source_rgb(1, 0, 0) #black
        # cr.rectangle(0, 0, width, height)
        # cr.fill()

        cr.set_source_rgb(1, 1, 1) #white
        cr.set_line_width(0.04)
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(0.07)
        cr.move_to(0.5, 0.5)
        cr.show_text(self.text)
        cr.stroke()

        wx.DC.Blit(
            dcp,
            0, 0,  # destination point
            self.ClientSize[0],  # size of area to copy
            self.ClientSize[1],  # size of area to copy
            dcb,  # source
            0, 0  # source point
        )

    #Change what text is shown
    def SetText(self, text):
        self.text = text
        self.Refresh()

if haveCairo:
    app = wx.App(False)
    theFrame = MyFrame(None, 'Barebones Cairo Example')
    app.MainLoop()
else:
    print "Error! PyCairo or a related dependency was not found"
