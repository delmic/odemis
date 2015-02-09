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

FIT_NONE = 0
FIT_HORZ = 1
FIT_VERT = 2


class Canvas(glcanvas.GLCanvas):

    SQAURE = [(-1, 1), (1, 1), (1, -1), (-1, -1)]

    def __init__(self, parent):
        glcanvas.GLCanvas.__init__(self, parent, -1)
        self.init = False
        self.context = glcanvas.GLContext(self)

        self.fit = FIT_HORZ | FIT_VERT

        self._texture_ids = []

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

    def do_set_viewport(self):
        w, h = self.GetClientSize()
        self.SetCurrent(self.context)
        glViewport(0, 0, w, h)

        self._set_projection()

    def _set_projection(self):
        glMatrixMode(GL_PROJECTION)
        glLoadIdentity()
        w, h = self.GetClientSize()

        if self.fit == FIT_HORZ | FIT_VERT:
            glOrtho(-1, 1, 1, -1, -1, 1)
        elif self.fit & FIT_VERT == FIT_VERT:
            aspect_ratio = w / float(h)
            glOrtho(-1 * aspect_ratio, 1 * aspect_ratio, 1, -1, -1, 1)
        elif self.fit & FIT_HORZ == FIT_HORZ:
            aspect_ratio = h / float(w)
            glOrtho(-1, 1, 1 * aspect_ratio, -1 * aspect_ratio, -1, 1)

    def set_image(self, img):
        self._texture_ids = [glGenTextures(1)]
        glBindTexture(GL_TEXTURE_2D, self._texture_ids[0])

        w, h, d = img.shape

        if d == 3:
            frmt = GL_RGB
        else:
            frmt = GL_RGBA

        glTexImage2D(GL_TEXTURE_2D, 0, frmt, w, h, 0, frmt, GL_UNSIGNED_BYTE, img)

        glTexParameter(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST)
        glTexParameter(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST)

        glTexParameter(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP)
        glTexParameter(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP)

    def init_gl(self):
        self._set_projection()

    def on_draw(self):

        # glMatrixMode(GL_PROJECTION)
        # glLoadIdentity()
        #
        # glScalef(self.z, self.z, 1)

        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)

        if not self._texture_ids:
            return

        glMatrixMode(GL_MODELVIEW)
        glLoadIdentity()

        glEnable(GL_TEXTURE_2D)
        glBindTexture(GL_TEXTURE_2D, self._texture_ids[0])

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
        self.SetSize((500, 400))
        self.Center()
        self.Show()

        self.canvas.set_image(generate_img_data(100, 100, 3))


if haveGLCanvas and haveOpenGL:
    app = wx.App(False)
    win = MainWindow(None)
    app.MainLoop()

# END THREAD RENDER TEST

#=============================================================================
