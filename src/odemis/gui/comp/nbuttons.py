import wx
from wx.lib.buttons import GenBitmapButton
import odemis.gui.img.data as data

def resize_bmp(btn_size, bmp, bg_color):
    """ Resize the bitmap image so it will match the given button size

    :param btn_size: Size tuple (w, h)
    :param bmp: Bitmap image (wx.Bitmap)
    :rtype: wx.Bitmap
    """

    if btn_size:
        btn_width, _ = btn_size
        img_width, img_height = bmp.GetSize()

        if 0 < btn_width != img_width:

            if 1:
                new_img = bmp.ConvertToImage()
                l = new_img.GetSubImage((0, 0, 3, 48)).ConvertToBitmap()
                m = new_img.GetSubImage((3, 0, 3, 48)).Rescale(btn_width - 6, 48).ConvertToBitmap()
                r = new_img.GetSubImage((6, 0, 3, 48)).ConvertToBitmap()

                src_dc = wx.MemoryDC()
                src_dc.SelectObjectAsSource(bmp)

                dst_bmp = wx.EmptyBitmap(btn_width, 48)
                dst_dc = wx.MemoryDC()
                dst_dc.SelectObject(dst_bmp)
                dst_dc.SetBackground(wx.Brush(bg_color))
                dst_dc.Clear()

                dst_dc.DrawBitmap(l, 0, 0, True)
                dst_dc.DrawBitmap(m, 3, 0, True)
                dst_dc.DrawBitmap(r, btn_width - 3, 0)

            else:
                src_dc = wx.MemoryDC()
                src_dc.SelectObjectAsSource(bmp)

                dst_bmp = wx.EmptyBitmap(btn_width, 48, 32)
                dst_dc = wx.MemoryDC()
                dst_dc.SelectObject(dst_bmp)
                dst_dc.SetBackground(wx.Brush(bg_color))
                dst_dc.Clear()

                dst_dc.Blit(0, 0,
                            3, 48,
                            src_dc,
                            0, 0)

                dst_dc.StretchBlit(3, 0,
                                   btn_width - 3, 48,
                                   src_dc,
                                   3, 0,
                                   3, 48)

                dst_dc.Blit(btn_width - 3, 0,
                            3, 48,
                            src_dc,
                            6, 0)


            dst_dc.SelectObject(wx.NullBitmap)

            return dst_bmp

    return bmp


class ImageButton(GenBitmapButton):

    def __init__(self, *args, **kwargs):
        """ If the background_parent keyword argument is provided, it will be
        used to determine the background colour of the button. Otherwise, the
        direct parent will be used.

        :param parent: (wx.Window) parent window
        :param id: (int) button id (optional)
        :param bitmap: (wx.Bitmap) default button face. Use `SetBitmaps` to set
                the other faces (e.g. hover, active)
        :param pos: (int, int)) button position
        :param size: (int, int) button size
        :param background_parent: (wx.Window) any parent higher up in the
                hierarchy from which to pick the background colour. (optional)
        :param label_delta: (int) the number of pixels to move button text down
                and to the right when it is pressed, to create an indentation
                effect. (This is used by subclasses that allow text to be
                displayed)

        """

        kwargs['style'] = kwargs.get('style', 0) | wx.NO_BORDER

        bmp = args[2] if len(args) >= 3 else kwargs.get('bitmap', None)

        # Set the size of the button to match the bmp
        if len(args) >= 5:
            args[4] = (-1, bmp.Size.y)
        else:
            kwargs['size'] = (-1, bmp.Size.y)

        self.base_bmp = None
        GenBitmapButton.__init__(self, *args, **kwargs)

    def OnSize(self, evt):
        super(GenBitmapButton, self).OnSize(evt)

        if not self.base_bmp:
            self.base_bmp = self.bmpLabel
            self.base_sel_bmp = self.bmpSelected

        self.bmpLabel = resize_bmp(self.Size, self.base_bmp, self.Parent.GetBackgroundColour())

        if self.base_sel_bmp:
            self.bmpSelected = resize_bmp(self.Size, self.base_sel_bmp, self.Parent.GetBackgroundColour())

        # src_dc = wx.MemoryDC()
        # src_dc.SelectObjectAsSource(self.base_bmp)
        # print "Loaded %s x %s src bmp" % (self.base_bmp.Size.x, self.base_bmp.Size.y)
        #
        # dst_bmp = wx.EmptyBitmap(self.ClientSize.x, 48)
        # print "Created %s x %s dst bmp" % (dst_bmp.Size.x, dst_bmp.Size.y)
        # dst_dc = wx.MemoryDC()
        # dst_dc.SelectObject(dst_bmp)
        # dst_dc.Clear()
        #
        # dst_dc.Blit(0, 0,
        #             3, 48,
        #             src_dc,
        #             0, 0)
        #
        # # StretchBlit(self, xdest, ydest, dstWidth, dstHeight, source, xsrc, ysrc, srcWidth,
        # # srcHeight, logicalFunc=COPY, useMask=False, xsrcMask=DefaultCoord, ysrcMask=DefaultCoord)
        # dst_dc.StretchBlit(3, 0,
        #                    397, 48,
        #                    src_dc,
        #                    3, 0,
        #                    3, 48)
        #
        # dst_dc.Blit(self.ClientSize.x - 3, 0,
        #             3, 48,
        #             src_dc,
        #             self.base_bmp.Size.x - 3, 0,
        #             useMask=True)
        #
        # src_dc.SelectObject(wx.NullBitmap)
        # dst_dc.SelectObject(wx.NullBitmap)
        #
        # self.bmpLabel = dst_bmp

    def DrawLabel(self, dc, width, height, dx=0, dy=0):
        bmp = self.bmpLabel

        if self.bmpDisabled and not self.IsEnabled():
            bmp = self.bmpDisabled
        if self.bmpFocus and self.hasFocus:
            bmp = self.bmpFocus
        if self.bmpSelected and not self.up:
            bmp = self.bmpSelected

        dc.DrawBitmap(bmp, 0, 0)
