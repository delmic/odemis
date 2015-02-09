import wx
from OpenGL.raw.GLU import gluBuild2DMipmaps, gluPerspective
from odemis.gui.img.data import gettest_10x10Image
from odemis.gui.test import generate_img_data
from scipy.misc import lena
import numpy as np
import numpy.random as rdn

try:
    from wx import glcanvas
    haveGLCanvas = True
except ImportError:
    haveGLCanvas = False

try:
    # The Python OpenGL package can be found at
    # http://PyOpenGL.sourceforge.net/
    from OpenGL.GL import *
    from OpenGL.GLUT import *
    import OpenGL.arrays.vbo as glvbo
    haveOpenGL = True
except ImportError:
    haveOpenGL = False


# class MicCanvas(glcanvas.GLCanvas):
#
#     def __init__(self, parent):
#         glcanvas.GLCanvas.__init__(self, parent, -1)
#         self.init = False
#         self.context = glcanvas.GLContext(self)
#         self.lastx = self.x = 30
#         self.lasty = self.y = 30
#         self.size = None
#
#         self.z = -1.0
#
#         self.data = np.array(.2*rdn.randn(100000, 2), dtype=np.float32)
#         self.count = self.data.shape[0]
#
#         self.Bind(wx.EVT_ERASE_BACKGROUND, self.OnEraseBackground)
#         self.Bind(wx.EVT_SIZE, self.OnSize)
#         self.Bind(wx.EVT_PAINT, self.OnPaint)
#
#         self.Bind(wx.EVT_LEFT_DOWN, self.OnMouseDown)
#         self.Bind(wx.EVT_LEFT_UP, self.OnMouseUp)
#         self.Bind(wx.EVT_MOTION, self.OnMouseMotion)
#
#         self.Bind(wx.EVT_MOUSEWHEEL, self.OnMouseWheel)
#
#     def OnEraseBackground(self, event):
#         pass  # Do nothing, to avoid flashing on MSW.
#
#     def OnSize(self, event):
#         wx.CallAfter(self.DoSetViewport)
#         event.Skip()
#
#     def DoSetViewport(self):
#         size = self.size = self.GetClientSize()
#         self.SetCurrent(self.context)
#         glViewport(0, 0, size.width, size.height)
#
#     def OnPaint(self, event):
#         dc = wx.PaintDC(self)
#         self.SetCurrent(self.context)
#         if not self.init:
#             self.InitGL()
#             self.init = True
#         self.OnDraw()
#
#     def InitGL(self):
#         # Set for 2D
#         glMatrixMode(GL_PROJECTION)
#         glLoadIdentity()
#         glOrtho(-1, 1, 1, -1, -1, 1)
#
#         # glClearColor(0.5, 0, 0, 1.0)
#         # create a Vertex Buffer Object with the specified data
#         self.vbo = glvbo.VBO(self.data)
#
#     def OnMouseDown(self, evt):
#         self.CaptureMouse()
#         self.x, self.y = self.lastx, self.lasty = evt.GetPosition()
#
#     def OnMouseUp(self, evt):
#         self.ReleaseMouse()
#
#     def OnMouseMotion(self, evt):
#         if evt.Dragging() and evt.LeftIsDown():
#             self.lastx, self.lasty = self.x, self.y
#             self.x, self.y = evt.GetPosition()
#             self.Refresh(False)
#
#     def OnMouseWheel(self, evt):
#         glMatrixMode(GL_MODELVIEW)
#
#         if evt.GetWheelRotation() > 0:
#             glTranslatef(0.0, 0.0, -0.02)
#         else:
#             glTranslatef(0.0, 0.0, 0.02)
#         self.Refresh()
#
#
# class CubeCanvas(MicCanvas):
#     def InitGL(self):
#         # set viewing projection
#         glMatrixMode(GL_PROJECTION)
#         glFrustum(0, 1, 0, 1, 1.0, 3.0)
#
#         # position viewer
#         glMatrixMode(GL_MODELVIEW)
#         glTranslatef(0.0, 0.0, self.z)
#
#         # position object
#         # glRotatef(self.y, 1.0, 0.0, 0.0)
#         # glRotatef(self.x, 0.0, 1.0, 0.0)
#
#         glEnable(GL_DEPTH_TEST)
#         glEnable(GL_LIGHTING)
#         glEnable(GL_LIGHT0)
#
#
#         w, h = 100, 100
#         # self.data = img.GetData()
#         self.data = generate_img_data(10, 10, 3)
#         self.texid = glGenTextures(1)
#         glBindTexture(GL_TEXTURE_2D, self.texid)
#         gluBuild2DMipmaps(GL_TEXTURE_2D, 4, w, h, GL_RGB, GL_UNSIGNED_BYTE, self.data)
#
#         glTexParameter(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST)
#         glTexParameter(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST)
#
#         # map the image data to the texture. note that if the input
#         # type is GL_FLOAT, the values must be in the range [0..1]
#         # glTexImage2D(GL_TEXTURE_2D, 0, GL_RGB, w, h, 0, GL_LUMINANCE, GL_UNSIGNED_BYTE, self.data)
#
#     def OnDraw(self):
#         # clear color and depth buffers
#
#         glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
#
#         # make the OpenGL context associated with this canvas the current one
#         self.SetCurrent()
#
#         # set the viewport and projection
#         w, h = self.GetSize()
#         # glViewport(0, 0, w, h)
#
#
#         glMatrixMode(GL_PROJECTION)
#         # glLoadIdentity()
#         # glOrtho(0, 1, 1, 0, 0, 1)
#
#         # enable textures, bind to our texture
#         glEnable(GL_TEXTURE_2D)
#         glBindTexture(GL_TEXTURE_2D, self.texid)
#
#         glBegin(GL_QUADS)
#
#         glTexCoord2f(0, 1)
#         glVertex2f(0, 1)
#
#         glTexCoord2f(0, 0)
#         glVertex2f(0, 0)
#
#         glTexCoord2f(1, 0)
#         glVertex2f(1, 0)
#
#         glTexCoord2f(1, 1)
#         glVertex2f(1, 1)
#
#         glEnd()
#
#         glDisable(GL_TEXTURE_2D)

        # # draw six faces of a cube
        # glBegin(GL_QUADS)
        #
        # glNormal3f( 0.0, 0.0, 1.0)
        # glVertex3f( 0.5, 0.5, 0.5)
        # glVertex3f(-0.5, 0.5, 0.5)
        # glVertex3f(-0.5,-0.5, 0.5)
        # glVertex3f( 0.5,-0.5, 0.5)
        #
        # glNormal3f( 0.0, 0.0,-1.0)
        # glVertex3f(-0.5,-0.5,-0.5)
        # glVertex3f(-0.5, 0.5,-0.5)
        # glVertex3f( 0.5, 0.5,-0.5)
        # glVertex3f( 0.5,-0.5,-0.5)
        #
        # glNormal3f( 0.0, 1.0, 0.0)
        # glVertex3f( 0.5, 0.5, 0.5)
        # glVertex3f( 0.5, 0.5,-0.5)
        # glVertex3f(-0.5, 0.5,-0.5)
        # glVertex3f(-0.5, 0.5, 0.5)
        #
        # glNormal3f( 0.0,-1.0, 0.0)
        # glVertex3f(-0.5,-0.5,-0.5)
        # glVertex3f( 0.5,-0.5,-0.5)
        # glVertex3f( 0.5,-0.5, 0.5)
        # glVertex3f(-0.5,-0.5, 0.5)
        #
        # glNormal3f( 1.0, 0.0, 0.0)
        # glVertex3f( 0.5, 0.5, 0.5)
        # glVertex3f( 0.5,-0.5, 0.5)
        # glVertex3f( 0.5,-0.5,-0.5)
        # glVertex3f( 0.5, 0.5,-0.5)
        #
        # glNormal3f(-1.0, 0.0, 0.0)
        # glVertex3f(-0.5,-0.5,-0.5)
        # glVertex3f(-0.5,-0.5, 0.5)
        # glVertex3f(-0.5, 0.5, 0.5)
        # glVertex3f(-0.5, 0.5,-0.5)
        # glEnd()
        #
        # if self.size is None:
        #     self.size = self.GetClientSize()
        # w, h = self.size
        # w = max(w, 1.0)
        # h = max(h, 1.0)
        # xScale = 180.0 / w
        # yScale = 180.0 / h
        # glRotatef((self.y - self.lasty) * yScale, 1.0, 0.0, 0.0);
        # glRotatef((self.x - self.lastx) * xScale, 0.0, 1.0, 0.0);

        # self.SwapBuffers()


class Canvas(glcanvas.GLCanvas):

    SQAURE = [(-1, 1), (1, 1), (1, -1), (-1, -1)]

    def __init__(self, parent):
        glcanvas.GLCanvas.__init__(self, parent, -1)
        self.init = False
        self.context = glcanvas.GLContext(self)

        self.z = 1.0
        self.texid = 0

        self.data = np.array(.2*rdn.randn(100000, 2), dtype=np.float32)
        self.count = self.data.shape[0]

        self.Bind(wx.EVT_ERASE_BACKGROUND, self.on_erase_background)
        self.Bind(wx.EVT_SIZE, self.on_size)
        self.Bind(wx.EVT_PAINT, self.on_paint)

    def on_erase_background(self, evt):
        pass  # Do nothing, to avoid flashing on MSW.

    def on_size(self, event):
        wx.CallAfter(self.do_set_viewport)
        event.Skip()

    def on_paint(self, evt):
        self.SetCurrent(self.context)
        if not self.init:
            self.init_gl()
            self.init = True
        self.on_draw()

    def on_mouse_wheel(self, evt):
        if evt.GetWheelRotation() > 0:
            self.z += -0.05
        else:
            self.z += 0.05
        if self.z < 0:
            self.z = 0

        self.Refresh()

    def do_set_viewport(self):
        w, h = self.GetClientSize()
        self.SetCurrent(self.context)
        glViewport(0, 0, w, h)

    def init_gl(self):
        glClearDepth(1.0)
        glMatrixMode(GL_PROJECTION)
        glLoadIdentity()
        glOrtho(-1, 1, 1, -1, 0, 50)

        # Texture

        self.texid = glGenTextures(1)
        glBindTexture(GL_TEXTURE_2D, self.texid)
        self.data = gettest_10x10Image().GetData()
        w, h = gettest_10x10Image().GetSize()
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGB, w, h, 0, GL_RGB, GL_UNSIGNED_BYTE, self.data)

        glTexParameter(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST)
        glTexParameter(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST)

        glTexParameter(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP)
        glTexParameter(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP)

    def on_draw(self):

        glMatrixMode(GL_PROJECTION)
        glLoadIdentity()

        glScalef(self.z, self.z, 1)

        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)

        glMatrixMode(GL_MODELVIEW)
        glLoadIdentity()

        glEnable(GL_TEXTURE_2D)

        glBegin(GL_QUADS)

        glTexCoord2f(0, 1)
        glVertex2f(-1, -1)

        glTexCoord2f(0, 0)
        glVertex2f(-1, 1)

        glTexCoord2f(1, 0)
        glVertex2f(1, 1)

        glTexCoord2f(1, 1)
        glVertex2f(1, -1)

        glEnd()

        glDisable(GL_TEXTURE_2D)

        self.SwapBuffers()


class MainWindow(wx.Frame):
    def __init__(self, *args, **kwargs):
        wx.Frame.__init__(self, *args, **kwargs)
        self.canvas = Canvas(self)
        # self.canvas.SetMinSize((300, 300))
        self.SetSize((500, 400))
        self.Center()
        self.Show()


if haveGLCanvas and haveOpenGL:
    app = wx.App(False)
    win = MainWindow(None)
    app.MainLoop()

# END THREAD RENDER TEST

#=============================================================================
