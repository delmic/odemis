
# This module is used to construct Delmic's custom FoldPanelBar according to
# the definition in the XRC configuration file.
#
# This module is used both by Odemis' GUI and XRCED.

import wx.xrc as xrc
import odemis.gui.comp.foldpanelbar as fpb
from wx.tools.XRCed.globals import TRACE

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

    def CanHandle(self, node):
        # return not self._isInside and self.IsOfClass(node, 'wx.lib.foldpanelbar.FoldPanelBar') or \
        #        self._isInside and self.IsOfClass(node, 'foldpanel')
        return not self._isInside \
               and self.IsOfClass(node, 'odemis.gui.comp.foldpanelbar.FoldPanelBar') \
               or self._isInside \
               and self.IsOfClass(node, 'foldpanel') \


    # Process XML parameters and create the object
    def DoCreateResource(self):
        TRACE('DoCreateResource: %s', self.GetClass())
        if self.GetClass() == 'foldpanel':
            n = self.GetParamNode('object')
            if n:
                old_ins = self._isInside
                self._isInside = False
                bar = self._w
                item = self.CreateResFromNode(n, bar, None)
                self._isInside = old_ins
                wnd = item
                if wnd:
                    item = bar.AddFoldPanel(self.GetText('label'),
                                            collapsed=self.GetBool('collapsed'))
                    bar.AddFoldPanelWindow(item, wnd)
            return wnd
        else:
            w = fpb.FoldPanelBar(self.GetParentAsWindow(),
                                 self.GetID(),
                                 self.GetPosition(),
                                 self.GetSize(),
                                 self.GetStyle(),
                                 self.GetStyle('exstyle'))
            self.SetupWindow(w)
            self._w = w
            old_ins = self._isInside
            self._isInside = True
            self.CreateChildren(w, True)
            self._isInside = old_ins
            return w

