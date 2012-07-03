
# This module is used to construct Delmic's custom FoldPanelBar according to
# the definition in the XRC configuration file.
#
# This module is used both by Odemis' GUI and XRCED.

import wx
import wx.lib.buttons
import wx.xrc as xrc
from wx.tools.XRCed.globals import TRACE

import odemis.gui.comp.foldpanelbar as fpb
import odemis.gui.comp.stream as strm
import odemis.gui.comp.buttons as btns
import odemis.gui.comp.text as txt

class FixedStreamPanelXmlHandler(xrc.XmlResourceHandler):
    def __init__(self):
        xrc.XmlResourceHandler.__init__(self)
        # Specify the styles recognized by objects of this type
        self.AddStyle("wxTAB_TRAVERSAL", wx.TAB_TRAVERSAL)
        #self.AddStyle("CP_GTK_EXPANDER", CP_GTK_EXPANDER)
        #self.AddStyle("CP_DEFAULT_STYLE", CP_DEFAULT_STYLE)
        #self.AddStyle("CP_NO_TLW_RESIZE", CP_NO_TLW_RESIZE)
        self.AddWindowStyles()

    # This method and the next one are required for XmlResourceHandlers
    def CanHandle(self, node):
        return self.IsOfClass(node, "odemis.gui.comp.stream.FixedStreamPanel")

    def DoCreateResource(self):
        assert self.GetInstance() is None

        # Now create the object
        panel = strm.FixedStreamPanel(self.GetParentAsWindow(),
                                      self.GetID(),
                                      self.GetText('label'),
                                      self.GetPosition(),
                                      self.GetSize(),
                                      self.GetStyle("style", wx.TAB_TRAVERSAL),
                                      #self.GetStyle('exstyle'),
                                      name=self.GetName(),
                                      collapsed=self.GetBool('collapsed')
                                      )

        # These two things should be done in either case:
        # Set standard window attributes
        self.SetupWindow(panel)
        panel.finalize()
        # Create any child windows of this node
        # deprecated: all children are hard-coded
        #self.CreateChildren(panel.get_panel())

        return panel

class CustomStreamPanelXmlHandler(xrc.XmlResourceHandler):
    def __init__(self):
        xrc.XmlResourceHandler.__init__(self)
        # Specify the styles recognized by objects of this type
        self.AddStyle("wxTAB_TRAVERSAL", wx.TAB_TRAVERSAL)
        #self.AddStyle("CP_GTK_EXPANDER", CP_GTK_EXPANDER)
        #self.AddStyle("CP_DEFAULT_STYLE", CP_DEFAULT_STYLE)
        #self.AddStyle("CP_NO_TLW_RESIZE", CP_NO_TLW_RESIZE)
        self.AddWindowStyles()

    # This method and the next one are required for XmlResourceHandlers
    def CanHandle(self, node):
        return self.IsOfClass(node, "odemis.gui.comp.stream.CustomStreamPanel")

    def DoCreateResource(self):
        assert self.GetInstance() is None

        # Now create the object
        panel = strm.CustomStreamPanel(self.GetParentAsWindow(),
                                      self.GetID(),
                                      self.GetText('label'),
                                      self.GetPosition(),
                                      self.GetSize(),
                                      self.GetStyle("style", wx.TAB_TRAVERSAL),
                                      #self.GetStyle('exstyle'),
                                      name=self.GetName(),
                                      collapsed=self.GetBool('collapsed')
                                      )

        # These two things should be done in either case:
        # Set standard window attributes
        self.SetupWindow(panel)
        panel.finalize()

        # Create any child windows of this node
        # deprecated: all children are hard-coded
        #self.CreateChildren(panel.get_panel())

        return panel

class FoldPanelBarXmlHandler(xrc.XmlResourceHandler):
    def __init__(self):
        xrc.XmlResourceHandler.__init__(self)
        # Standard styles
        self.AddWindowStyles()
        # Custom styles
        self.AddStyle('FPB_SINGLE_FOLD', fpb.FPB_SINGLE_FOLD)
        self.AddStyle('FPB_COLLAPSE_TO_BOTTOM', fpb.FPB_COLLAPSE_TO_BOTTOM)
        self.AddStyle('FPB_EXCLUSIVE_FOLD', fpb.FPB_EXCLUSIVE_FOLD)
        self.AddStyle('FPB_HORIZONTAL', fpb.FPB_HORIZONTAL)
        self.AddStyle('FPB_VERTICAL', fpb.FPB_VERTICAL)
        self._isInside = False
        self.current_foldpanelitem = None
        self.spacing = fpb.FPB_DEFAULT_SPACING
        self.left_spacing = fpb.FPB_DEFAULT_LEFTSPACING
        self.right_spacing = fpb.FPB_DEFAULT_RIGHTSPACING

    def CanHandle(self, node):
        # return not self._isInside and self.IsOfClass(node, 'wx.lib.foldpanelbar.FoldPanelBar') or \
        #        self._isInside and self.IsOfClass(node, 'foldpanel')
        return self.IsOfClass(node, 'odemis.gui.comp.foldpanelbar.FoldPanelBar') \
               or self._isInside and self.IsOfClass(node, 'odemis.gui.comp.foldpanelbar.FoldPanelItem') \


    # Process XML parameters and create the object
    def DoCreateResource(self):
        TRACE('DoCreateResource: %s', self.GetClass())

        if self.GetClass() == 'odemis.gui.comp.foldpanelbar.FoldPanelBar':
            w = fpb.FoldPanelBar(self.GetParentAsWindow(),
                                 self.GetID(),
                                 self.GetPosition(),
                                 self.GetSize(),
                                 self.GetStyle(),
                                 self.GetStyle('exstyle'))

            if self.HasParam('spacing'):
                self.spacing = self.GetLong('spacing')

            if self.HasParam('leftspacing'):
                self.left_spacing = self.GetLong('leftspacing')

            if self.HasParam('rightspacing'):
                self.right_spacing = self.GetLong('rightspacing')

            self.SetupWindow(w)
            self._w = w
            old_ins = self._isInside
            self._isInside = True
            # Note: CreateChildren will call this method again
            self.CreateChildren(w, True)
            self._isInside = old_ins

            parent = self._w.GetParent()
            if parent.__class__ == wx.ScrolledWindow:
                parent.EnableScrolling(False, True)
                parent.SetScrollbars(-1, 10, 1, 1)

            return w
        elif self.GetClass() == 'odemis.gui.comp.foldpanelbar.FoldPanelItem':
            item = self._w.AddFoldPanel(self.GetText('label'),
                                        collapsed=self.GetBool('collapsed'),
                                        id=self.GetID())
            self.current_foldpanelitem = item

            n = self.GetParamNode("object")
            wnd = None

            while n:
                #print "Creating Window ", n.GetPropVal('class', "")
                if n.Name != 'object':
                    n = n.Next
                    continue
                wnd = self.CreateResFromNode(n, self.current_foldpanelitem, None)
                if wnd:
                    self._w.AddFoldPanelWindow(self.current_foldpanelitem,
                                               wnd,
                                               spacing=self.spacing,
                                               leftSpacing=self.left_spacing,
                                               rightSpacing=self.right_spacing)
                n = n.Next

            # If the last one, was a window ctrl...
            if n and n.Name == 'object' and wnd:
                pass


        #wx.CallAfter(self._w.FitBar)

class FoldPanelXmlHandler(xrc.XmlResourceHandler):
    def __init__(self):
        xrc.XmlResourceHandler.__init__(self)
        # Standard styles
        self.AddWindowStyles()
        # Custom styles

    def CanHandle(self, node):
        # return not self._isInside and self.IsOfClass(node, 'wx.lib.foldpanelbar.FoldPanelBar') or \
        #        self._isInside and self.IsOfClass(node, 'foldpanel')
        return self.IsOfClass(node, 'odemis.gui.comp.foldpanelbar.FoldPanelBar') \
               or self.IsOfClass(node, 'odemis.gui.comp.foldpanelbar.FoldPanelItem') \


    # Process XML parameters and create the object
    def DoCreateResource(self):
        pass

class GenBitmapButtonHandler(xrc.XmlResourceHandler):
    def __init__(self):
        xrc.XmlResourceHandler.__init__(self)
        # Standard styles
        self.AddWindowStyles()
        # Custom styles

    def CanHandle(self, node):
        return self.IsOfClass(node, 'wx.lib.buttons.GenBitmapButton')

    # Process XML parameters and create the object
    def DoCreateResource(self):
        assert self.GetInstance() is None

        bmp = wx.NullBitmap
        if self.GetParamNode("bitmap"):
            bmp = self.GetBitmap("bitmap")

        w = wx.lib.buttons.GenBitmapButton(self.GetParentAsWindow(),
                                    self.GetID(),
                                    bmp,
                                    self.GetPosition(),
                                    self.GetSize(),
                                    self.GetStyle())

        if self.GetParamNode("selected"):
            bmp = self.GetBitmap("selected")
            w.SetBitmapSelected(bmp)

        if self.GetParamNode("focus"):
            bmp = self.GetBitmap("focus")
            w.SetBitmapFocus(bmp)

        if self.GetParamNode("disabled"):
            bmp = self.GetBitmap("disabled")
            w.SetBitmapDisabled(bmp)

        self.SetupWindow(w)
        return w

class ImageButtonHandler(xrc.XmlResourceHandler):

    def __init__(self):
        xrc.XmlResourceHandler.__init__(self)
        # Standard styles
        self.AddWindowStyles()
        # Custom styles

    def CanHandle(self, node):
        return self.IsOfClass(node, 'odemis.gui.comp.buttons.ImageButton')

    # Process XML parameters and create the object
    def DoCreateResource(self):
        assert self.GetInstance() is None

        bmp = wx.NullBitmap
        if self.GetParamNode("bitmap"):
            bmp = self.GetBitmap("bitmap")

        w = btns.ImageButton(self.GetParentAsWindow(),
                            self.GetID(),
                            bmp,
                            pos=self.GetPosition(),
                            size=self.GetSize(),
                            style=self.GetStyle())

        if self.GetParamNode("selected"):
            bmp = self.GetBitmap("selected")
            w.SetBitmapSelected(bmp)

        if self.GetParamNode("hover"):
            bmp = self.GetBitmap("hover")
            w.SetBitmapHover(bmp)

        if self.GetParamNode("focus"):
            bmp = self.GetBitmap("focus")
            w.SetBitmapFocus(bmp)


        if self.GetParamNode("disabled"):
            bmp = self.GetBitmap("disabled")
            w.SetBitmapDisabled(bmp)

        self.SetupWindow(w)
        return w

class PopupImageButtonHandler(xrc.XmlResourceHandler):

    def __init__(self):
        xrc.XmlResourceHandler.__init__(self)
        # Standard styles
        self.AddWindowStyles()
        # Custom styles

    def CanHandle(self, node):
        return self.IsOfClass(node, 'odemis.gui.comp.buttons.PopupImageButton')

    # Process XML parameters and create the object
    def DoCreateResource(self):
        assert self.GetInstance() is None

        bmp = wx.NullBitmap
        if self.GetParamNode("bitmap"):
            bmp = self.GetBitmap("bitmap")

        w = btns.PopupImageButton(self.GetParentAsWindow(),
                                self.GetID(),
                                bmp,
                                pos=self.GetPosition(),
                                size=self.GetSize(),
                                style=self.GetStyle())

        if self.GetParamNode("selected"):
            bmp = self.GetBitmap("selected")
            w.SetBitmapSelected(bmp)

        if self.GetParamNode("hover"):
            bmp = self.GetBitmap("hover")
            w.SetBitmapHover(bmp)

        if self.GetParamNode("focus"):
            bmp = self.GetBitmap("focus")
            w.SetBitmapFocus(bmp)


        if self.GetParamNode("disabled"):
            bmp = self.GetBitmap("disabled")
            w.SetBitmapDisabled(bmp)

        self.SetupWindow(w)
        return w

class SuggestTextCtrlHandler(xrc.XmlResourceHandler):

    def __init__(self):
        xrc.XmlResourceHandler.__init__(self)
        # Standard styles
        self.AddWindowStyles()
        # Custom styles

    def CanHandle(self, node):
        return self.IsOfClass(node, 'odemis.gui.comp.text.SuggestTextCtrl')

    # Process XML parameters and create the object
    def DoCreateResource(self):
        assert self.GetInstance() is None

        w = txt.SuggestTextCtrl(self.GetParentAsWindow(),
                                id=self.GetID(),
                                value=self.GetText('value'),
                                pos=self.GetPosition(),
                                size=self.GetSize(),
                                style=self.GetStyle(),
                                choices=[str(i) for i in range(2)])
        self.SetupWindow(w)
        return w

class UnitIntegerCtrlHandler(xrc.XmlResourceHandler):

    def __init__(self):
        xrc.XmlResourceHandler.__init__(self)
        # Standard styles
        self.AddWindowStyles()
        # Custom styles

    def CanHandle(self, node):
        return self.IsOfClass(node, 'odemis.gui.comp.text.UnitIntegerCtrl')

    # Process XML parameters and create the object
    def DoCreateResource(self):
        assert self.GetInstance() is None

        w = txt.UnitIntegerCtrl(self.GetParentAsWindow(),
                                id=self.GetID(),
                                value=self.GetText('value'),
                                pos=self.GetPosition(),
                                size=self.GetSize(),
                                style=self.GetStyle(),
                                unit=self.GetText('unit'),
                                min_val=self.GetLong('min'),
                                max_val=self.GetLong('max'))
        self.SetupWindow(w)
        return w


HANDLER_CLASS_LIST = [FixedStreamPanelXmlHandler,
                      CustomStreamPanelXmlHandler,
                      FoldPanelBarXmlHandler,
                      GenBitmapButtonHandler,
                      ImageButtonHandler,
                      PopupImageButtonHandler,
                      SuggestTextCtrlHandler,
                      UnitIntegerCtrlHandler]
