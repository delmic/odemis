# -*- coding: utf-8 -*-

"""
@author: Rinze de Laat

Copyright © 2012 Rinze de Laat, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License version 2 as published by the Free
Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

"""

# This module is used to construct Delmic's custom FoldPanelBar according to
# the definition in the XRC configuration file.
#
# This module is used both by Odemis' GUI and XRCED.

import ast
import logging
import odemis.gui.comp.buttons as btns
import odemis.gui.comp.foldpanelbar as fpb
import odemis.gui.comp.viewport as vport
import odemis.gui.comp.slider as slide
import odemis.gui.comp.stream as strm
import odemis.gui.comp.grid as grid
import odemis.gui.comp.text as txt
import odemis.gui.cont.tools as tools
from odemis.gui import img
import wx
import wx.xrc as xrc
import wx.adv


HANDLER_CLASS_LIST = []

##################################
# Fold Panel Bar related Handlers
##################################


class StreamPanelXmlHandler(xrc.XmlResourceHandler):
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
        capable = self.IsOfClass(node, "StreamPanel")

        return capable

    def DoCreateResource(self):
        assert self.GetInstance() is None

        parent_window = self.GetParentAsWindow()
        # Now create the object
        panel = strm.StreamPanel(
            parent_window,
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

        parent_window.add_stream_panel(panel)
        # Create any child windows of this node
        # deprecated: all children are hard-coded
        #self.CreateChildren(panel.get_panel())

        return panel
HANDLER_CLASS_LIST.append(StreamPanelXmlHandler)


class FoldPanelBarXmlHandler(xrc.XmlResourceHandler):
    def __init__(self):
        xrc.XmlResourceHandler.__init__(self)
        # Standard styles
        self.AddWindowStyles()
        #self._isInside = False

    def CanHandle(self, node):
        # return not self._isInside and self.IsOfClass(node, 'wx.lib.foldpanelbar.FoldPanelBar') or \
        #        self._isInside and self.IsOfClass(node, 'foldpanel')
        return self.IsOfClass(node, 'FoldPanelBar')

    # Process XML parameters and create the object
    def DoCreateResource(self):

        if self.GetClass() == 'FoldPanelBar':
            #print "Creating FoldpanelBar"
            w = fpb.FoldPanelBar(
                self.GetParentAsWindow(),
                self.GetID(),
                self.GetPosition(),
                self.GetSize(),
                self.GetStyle()
            )
            self.SetupWindow(w)

            parent = w.GetParent()
            if parent.__class__ == wx.ScrolledWindow:
                parent.EnableScrolling(False, True)
                parent.SetScrollbars(-1, 10, 1, 1)

            self.CreateChildren(w, False)

            return w
HANDLER_CLASS_LIST.append(FoldPanelBarXmlHandler)


class CaptionBarXmlHandler(xrc.XmlResourceHandler):
    def __init__(self):
        xrc.XmlResourceHandler.__init__(self)
        # Standard styles
        self.AddWindowStyles()
        # Custom styles

    def CanHandle(self, node):
        return self.IsOfClass(node, 'CaptionBar')


    # Process XML parameters and create the object
    def DoCreateResource(self):

        if self.GetClass() == 'CaptionBar':
            #print "Creating CaptionBar"
            parent = self.GetParentAsWindow()
            w = fpb.CaptionBar(
                            parent,
                            self.GetText('label'),
                            self.GetBool('collapsed')
            )
            self.SetupWindow(w)
            return w
HANDLER_CLASS_LIST.append(CaptionBarXmlHandler)


class FoldPanelItemXmlHandler(xrc.XmlResourceHandler):
    def __init__(self):
        xrc.XmlResourceHandler.__init__(self)
        # Standard styles
        self.AddWindowStyles()
        # Custom styles

    def CanHandle(self, node):
        return self.IsOfClass(node, 'FoldPanelItem')

    # Process XML parameters and create the object
    def DoCreateResource(self):

        if self.GetClass() == 'FoldPanelItem':
            parent = self.GetParentAsWindow()
            w = fpb.FoldPanelItem(parent,
                                  self.GetID(),
                                  self.GetPosition(),
                                  self.GetSize(),
                                  self.GetStyle(),
                                  self.GetText('label'),
                                  self.GetBool('collapsed'),
                                  nocaption=self.GetBool('nocaption'))
            self.SetupWindow(w)
            self.CreateChildren(w, False)

            # Move all the FoldPanelItem children to the main sizer
            w.children_to_sizer()
            parent.add_item(w)
            return w
HANDLER_CLASS_LIST.append(FoldPanelItemXmlHandler)


class StreamBarXmlHandler(xrc.XmlResourceHandler):
    def __init__(self):
        xrc.XmlResourceHandler.__init__(self)
        # Standard styles
        self.AddWindowStyles()
        # Custom styles

    def CanHandle(self, node):
        return self.IsOfClass(node, 'StreamBar')

    # Process XML parameters and create the object
    def DoCreateResource(self):

        if self.GetClass() == 'StreamBar':
            parent = self.GetParentAsWindow()
            w = strm.StreamBar(parent,
                                 self.GetID(),
                                 self.GetPosition(),
                                 self.GetSize(),
                                 self.GetStyle(),
                                 add_button=self.GetBool('add_button'))
            self.SetupWindow(w)
            # 'Dirty' fix for the hard coded 'add stream' child button
            if self.GetBool('add_button'):
                w.btn_add_stream.SetBackgroundColour(w.GetBackgroundColour())
            parent.add_item(w)
            return w
HANDLER_CLASS_LIST.append(StreamBarXmlHandler)


class FastEMProjectBarXmlHandler(xrc.XmlResourceHandler):
    def __init__(self):
        xrc.XmlResourceHandler.__init__(self)
        # Standard styles
        self.AddWindowStyles()
        # Custom styles

    def CanHandle(self, node):
        return self.IsOfClass(node, 'FastEMProjectList')

    # Process XML parameters and create the object
    def DoCreateResource(self):

        if self.GetClass() == 'FastEMProjectList':
            parent = self.GetParentAsWindow()
            w = strm.FastEMProjectList(parent,
                                       self.GetID(),
                                       self.GetPosition(),
                                       self.GetSize(),
                                       self.GetStyle(),
                                       add_button=self.GetBool('add_button'))
            self.SetupWindow(w)
            # 'Dirty' fix for the hard coded 'add stream' child button
            if self.GetBool('add_button'):
                w.btn_add_project.SetBackgroundColour(w.GetBackgroundColour())
            parent.add_item(w)
            return w
HANDLER_CLASS_LIST.append(FastEMProjectBarXmlHandler)


class FastEMCalibrationBarXmlHandler(xrc.XmlResourceHandler):
    def __init__(self):
        xrc.XmlResourceHandler.__init__(self)
        # Standard styles
        self.AddWindowStyles()
        # Custom styles

    def CanHandle(self, node):
        return self.IsOfClass(node, 'FastEMCalibrationPanelHeader')

    # Process XML parameters and create the object
    def DoCreateResource(self):

        if self.GetClass() == 'FastEMCalibrationPanelHeader':
            parent = self.GetParentAsWindow()
            w = strm.FastEMCalibrationPanelHeader(parent,
                                                  self.GetID(),
                                                  self.GetPosition(),
                                                  self.GetSize(),
                                                  self.GetStyle(),
                                                  add_button=self.GetBool('add_button'))
            self.SetupWindow(w)
            parent.add_item(w)
            return w
HANDLER_CLASS_LIST.append(FastEMCalibrationBarXmlHandler)


class FastEMAlignmentBarXmlHandler(xrc.XmlResourceHandler):
    def __init__(self):
        xrc.XmlResourceHandler.__init__(self)
        # Standard styles
        self.AddWindowStyles()
        # Custom styles

    def CanHandle(self, node):
        return self.IsOfClass(node, 'FastEMAlignmentBar')

    # Process XML parameters and create the object
    def DoCreateResource(self):

        if self.GetClass() == 'FastEMAlignmentBar':
            parent = self.GetParentAsWindow()
            w = strm.FastEMAlignmentBar(parent,
                                        self.GetID(),
                                        self.GetPosition(),
                                        self.GetSize(),
                                        self.GetStyle(),
                                        add_button=self.GetBool('add_button'))
            self.SetupWindow(w)
            parent.add_item(w)
            return w
HANDLER_CLASS_LIST.append(FastEMAlignmentBarXmlHandler)


class FastEMSelectionPanelXmlHandler(xrc.XmlResourceHandler):
    def __init__(self):
        xrc.XmlResourceHandler.__init__(self)
        # Standard styles
        self.AddWindowStyles()
        # Custom styles

    def CanHandle(self, node):
        return self.IsOfClass(node, 'FastEMSelectionPanel')

    # Process XML parameters and create the object
    def DoCreateResource(self):

        if self.GetClass() == 'FastEMSelectionPanel':
            parent = self.GetParentAsWindow()
            w = strm.FastEMOverviewSelectionPanel(parent,
                                                  self.GetID(),
                                                  self.GetPosition(),
                                                  self.GetSize(),
                                                  self.GetStyle())
            self.SetupWindow(w)
            #parent.add_item(w)
            return w
HANDLER_CLASS_LIST.append(FastEMSelectionPanelXmlHandler)


class _ImageButtonHandler(xrc.XmlResourceHandler):

    klass = None

    def __init__(self):
        xrc.XmlResourceHandler.__init__(self)
        # Standard styles
        self.AddWindowStyles()
        # Custom styles
        self.AddStyle('wxALIGN_LEFT', wx.ALIGN_LEFT)
        self.AddStyle('wxALIGN_RIGHT', wx.ALIGN_RIGHT)
        self.AddStyle('wxALIGN_CENTRE', wx.ALIGN_CENTRE)

    def CanHandle(self, node):
        return self.IsOfClass(node, self.klass.__name__)

    # Process XML parameters and create the object
    def DoCreateResource(self):
        assert self.GetInstance() is None

        icon = wx.NullBitmap
        if self.GetParamNode("icon"):
            icon = self.GetBitmap("icon")

        icon_on = wx.NullBitmap
        if self.GetParamNode("icon_on"):
            icon_on = self.GetBitmap("icon_on")

        height = int(self.GetText('height')) if self.GetText('height') else None

        try:
            kwargs = {
                "icon": icon,
                "icon_on": icon_on,
                "pos": self.GetPosition(),
                "size": self.GetSize(),
                "style": self.GetStyle(),
                "face_colour": self.GetText('face_colour'),
                "height": height,
            }

            if self.GetParamNode("label"):
                kwargs["label"] = self.GetText('label')
            if self.GetParamNode("active_colour"):
                kwargs["active_colour"] = self.GetText('active_colour')

            w = self.klass(
                self.GetParentAsWindow(),
                self.GetID(),
                **kwargs)

        except ValueError:
            print("Failed to create ImageButton %s" % (self.GetName(),))
            raise

        self.SetupWindow(w)
        return w


class ImageButtonHandler(_ImageButtonHandler):
    klass = btns.ImageButton
HANDLER_CLASS_LIST.append(ImageButtonHandler)


class ImageToggleButtonHandler(_ImageButtonHandler):
    klass = btns.ImageToggleButton
HANDLER_CLASS_LIST.append(ImageToggleButtonHandler)


class ImageTextButtonHandler(_ImageButtonHandler):
    klass = btns.ImageTextButton
HANDLER_CLASS_LIST.append(ImageTextButtonHandler)


class ImageTextToggleButtonHandler(_ImageButtonHandler):
    klass = btns.ImageTextToggleButton
HANDLER_CLASS_LIST.append(ImageTextToggleButtonHandler)


class GraphicRadioButtonHandler(_ImageButtonHandler):
    klass = btns.GraphicRadioButton
HANDLER_CLASS_LIST.append(GraphicRadioButtonHandler)


class TabButtonHandler(_ImageButtonHandler):
    klass = btns.TabButton
HANDLER_CLASS_LIST.append(TabButtonHandler)


class ViewButtonHandler(_ImageButtonHandler):
    klass = btns.ViewButton
HANDLER_CLASS_LIST.append(ViewButtonHandler)


class SuggestTextCtrlHandler(xrc.XmlResourceHandler):

    def __init__(self):
        xrc.XmlResourceHandler.__init__(self)
        # Standard styles
        self.AddWindowStyles()
        # Custom styles

    def CanHandle(self, node):
        return self.IsOfClass(node, 'SuggestTextCtrl')

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
HANDLER_CLASS_LIST.append(SuggestTextCtrlHandler)


class UnitIntegerCtrlHandler(xrc.XmlResourceHandler):

    def __init__(self):
        xrc.XmlResourceHandler.__init__(self)
        # Standard styles
        self.AddWindowStyles()
        # Custom styles

    def CanHandle(self, node):
        return self.IsOfClass(node, 'UnitIntegerCtrl')

    # Process XML parameters and create the object
    def DoCreateResource(self):
        assert self.GetInstance() is None

        val = int(self.GetText('value'))

        w = txt.UnitIntegerCtrl(self.GetParentAsWindow(),
                                id=self.GetID(),
                                value=val,
                                pos=self.GetPosition(),
                                size=self.GetSize(),
                                style=self.GetStyle(),
                                unit=self.GetText('unit'),
                                min_val=self.GetLong('min'),
                                max_val=self.GetLong('max'))
        self.SetupWindow(w)
        return w
HANDLER_CLASS_LIST.append(UnitIntegerCtrlHandler)


class UnitFloatCtrlHandler(xrc.XmlResourceHandler):

    def __init__(self):
        xrc.XmlResourceHandler.__init__(self)
        # Standard styles
        self.AddWindowStyles()
        # Custom styles

    def CanHandle(self, node):
        return self.IsOfClass(node, 'UnitFloatCtrl')

    # Process XML parameters and create the object
    def DoCreateResource(self):
        assert self.GetInstance() is None

        val = self.GetText('value').strip()
        val = float(val) if val else 0.0

        w = txt.UnitFloatCtrl(self.GetParentAsWindow(),
                              id=self.GetID(),
                              value=val,
                              pos=self.GetPosition(),
                              size=self.GetSize(),
                              style=self.GetStyle(),
                              unit=self.GetText('unit'),
                              min_val=self.GetLong('min'),
                              max_val=self.GetLong('max'),
                              key_step=self.GetFloat('key_step'),
                              accuracy=self.GetFloat('accuracy'))
        self.SetupWindow(w)
        return w
HANDLER_CLASS_LIST.append(UnitFloatCtrlHandler)


##################################
# Canvas Handlers
##################################

class CameraViewportXmlHandler(xrc.XmlResourceHandler):

    klass = vport.CameraViewport

    def __init__(self):
        xrc.XmlResourceHandler.__init__(self)
        # Specify the styles recognized by objects of this type
        self.AddStyle("wxTAB_TRAVERSAL", wx.TAB_TRAVERSAL)
        self.AddWindowStyles()

    # This method and the next one are required for XmlResourceHandlers
    def CanHandle(self, node):
        return self.IsOfClass(node, "CameraViewport")

    def DoCreateResource(self):
        assert self.GetInstance() is None

        # Now create the object
        panel = self.klass(self.GetParentAsWindow(),
                           id=self.GetID(),
                           pos=self.GetPosition(),
                           size=self.GetSize(),
                           style=self.GetStyle())
        self.SetupWindow(panel)
        return panel
HANDLER_CLASS_LIST.append(CameraViewportXmlHandler)


class FixedOverviewViewportXmlHandler(xrc.XmlResourceHandler):

    klass = vport.FixedOverviewViewport

    def __init__(self):
        xrc.XmlResourceHandler.__init__(self)
        # Specify the styles recognized by objects of this type
        self.AddStyle("wxTAB_TRAVERSAL", wx.TAB_TRAVERSAL)
        self.AddWindowStyles()

    # This method and the next one are required for XmlResourceHandlers
    def CanHandle(self, node):
        return self.IsOfClass(node, "FixedOverviewViewport")

    def DoCreateResource(self):
        assert self.GetInstance() is None

        # Now create the object
        panel = self.klass(self.GetParentAsWindow(),
                           id=self.GetID(),
                           pos=self.GetPosition(),
                           size=self.GetSize(),
                           style=self.GetStyle())
        self.SetupWindow(panel)
        return panel
HANDLER_CLASS_LIST.append(FixedOverviewViewportXmlHandler)


class MicroscopeViewportXmlHandler(xrc.XmlResourceHandler):

    klass = vport.MicroscopeViewport

    def __init__(self):
        xrc.XmlResourceHandler.__init__(self)
        # Specify the styles recognized by objects of this type
        self.AddStyle("wxTAB_TRAVERSAL", wx.TAB_TRAVERSAL)
        self.AddWindowStyles()

    # This method and the next one are required for XmlResourceHandlers
    def CanHandle(self, node):
        return self.IsOfClass(node, "MicroscopeViewport")

    def DoCreateResource(self):
        assert self.GetInstance() is None

        # Now create the object
        panel = self.klass(self.GetParentAsWindow(),
                           id=self.GetID(),
                           pos=self.GetPosition(),
                           size=self.GetSize(),
                           style=self.GetStyle())

        # Set standard window attributes
        self.SetupWindow(panel)
        # Create any child windows of this node
        self.CreateChildren(panel)
        return panel
HANDLER_CLASS_LIST.append(MicroscopeViewportXmlHandler)


class LiveViewportXmlHandler(MicroscopeViewportXmlHandler):

    klass = vport.LiveViewport

    def CanHandle(self, node):
        return self.IsOfClass(node, "LiveViewport")
HANDLER_CLASS_LIST.append(LiveViewportXmlHandler)


class RawLiveViewportXmlHandler(MicroscopeViewportXmlHandler):

    klass = vport.RawLiveViewport

    def CanHandle(self, node):
        return self.IsOfClass(node, "RawLiveViewport")
HANDLER_CLASS_LIST.append(RawLiveViewportXmlHandler)


class FeatureOverviewViewportXmlHandler(MicroscopeViewportXmlHandler):

    klass = vport.FeatureOverviewViewport

    def CanHandle(self, node):
        return self.IsOfClass(node, "FeatureOverviewViewport")
HANDLER_CLASS_LIST.append(FeatureOverviewViewportXmlHandler)


class ARAcquiViewportXmlHandler(MicroscopeViewportXmlHandler):

    klass = vport.ARAcquiViewport

    def CanHandle(self, node):
        return self.IsOfClass(node, "ARAcquiViewport")
HANDLER_CLASS_LIST.append(ARAcquiViewportXmlHandler)


class PointSpectrumViewportXmlHandler(MicroscopeViewportXmlHandler):

    klass = vport.PointSpectrumViewport

    def CanHandle(self, node):
        return self.IsOfClass(node, "PointSpectrumViewport")
HANDLER_CLASS_LIST.append(PointSpectrumViewportXmlHandler)


class ChronographViewportXmlHandler(MicroscopeViewportXmlHandler):

    klass = vport.ChronographViewport

    def CanHandle(self, node):
        return self.IsOfClass(node, "ChronographViewport")
HANDLER_CLASS_LIST.append(ChronographViewportXmlHandler)


class ThetaViewportXmlHandler(MicroscopeViewportXmlHandler):

    klass = vport.ThetaViewport

    def CanHandle(self, node):
        return self.IsOfClass(node, "ThetaViewport")
HANDLER_CLASS_LIST.append(ThetaViewportXmlHandler)


class ARLiveViewportXmlHandler(MicroscopeViewportXmlHandler):

    klass = vport.ARLiveViewport

    def CanHandle(self, node):
        return self.IsOfClass(node, "ARLiveViewport")
HANDLER_CLASS_LIST.append(ARLiveViewportXmlHandler)


class EKLiveViewportXmlHandler(MicroscopeViewportXmlHandler):

    klass = vport.EKLiveViewport

    def CanHandle(self, node):
        return self.IsOfClass(node, "EKLiveViewport")
HANDLER_CLASS_LIST.append(EKLiveViewportXmlHandler)


class AngularResolvedViewportXmlHandler(MicroscopeViewportXmlHandler):

    klass = vport.AngularResolvedViewport

    def CanHandle(self, node):
        return self.IsOfClass(node, "AngularResolvedViewport")
HANDLER_CLASS_LIST.append(AngularResolvedViewportXmlHandler)


class LineSpectrumViewportXmlHandler(MicroscopeViewportXmlHandler):

    klass = vport.LineSpectrumViewport

    def CanHandle(self, node):
        return self.IsOfClass(node, "LineSpectrumViewport")
HANDLER_CLASS_LIST.append(LineSpectrumViewportXmlHandler)


class TemporalSpectrumViewportXmlHandler(MicroscopeViewportXmlHandler):

    klass = vport.TemporalSpectrumViewport

    def CanHandle(self, node):
        return self.IsOfClass(node, "TemporalSpectrumViewport")


HANDLER_CLASS_LIST.append(TemporalSpectrumViewportXmlHandler)

class FastEMAcquisitionViewportXmlHandler(MicroscopeViewportXmlHandler):

    klass = vport.FastEMAcquisitionViewport

    def CanHandle(self, node):
        return self.IsOfClass(node, "FastEMAcquisitionViewport")
HANDLER_CLASS_LIST.append(FastEMAcquisitionViewportXmlHandler)

class FastEMOverviewViewportXmlHandler(MicroscopeViewportXmlHandler):

    klass = vport.FastEMOverviewViewport

    def CanHandle(self, node):
        return self.IsOfClass(node, "FastEMOverviewViewport")
HANDLER_CLASS_LIST.append(FastEMOverviewViewportXmlHandler)

class AngularSpectrumViewportXmlHandler(MicroscopeViewportXmlHandler):

    klass = vport.AngularSpectrumViewport

    def CanHandle(self, node):
        return self.IsOfClass(node, "AngularSpectrumViewport")


HANDLER_CLASS_LIST.append(AngularSpectrumViewportXmlHandler)

##################################
# Sliders
##################################

class UnitIntegerSliderHandler(xrc.XmlResourceHandler):
    def __init__(self):
        xrc.XmlResourceHandler.__init__(self)
        # Specify the styles recognized by objects of this type
        self.AddWindowStyles()

    # This method and the next one are required for XmlResourceHandlers
    def CanHandle(self, node):
        capable = self.IsOfClass(node, "UnitIntegerSlider")
        return capable

    def DoCreateResource(self):
        assert self.GetInstance() is None

        val = int(self.GetText('value') or 0)
        minv, maxv = self.GetLong('min'), self.GetLong('max')
        text_size = ast.literal_eval(self.GetText('text_size') or "50, -1")

        if minv == maxv:
            maxv = minv + 1

        # Now create the object
        slider = slide.UnitIntegerSlider(self.GetParentAsWindow(),
                                        wid=self.GetID(),
                                        pos=self.GetPosition(),
                                        size=self.GetSize(),
                                        style=self.GetStyle(),
                                        value=val,
                                        unit=self.GetText('unit'),
                                        min_val=minv,
                                        max_val=maxv,
                                        scale=self.GetText('scale'),
                                        t_size=text_size)
        self.SetupWindow(slider)
        return slider
HANDLER_CLASS_LIST.append(UnitIntegerSliderHandler)


class UnitFloatSliderHandler(xrc.XmlResourceHandler):
    def __init__(self):
        xrc.XmlResourceHandler.__init__(self)
        # Specify the styles recognized by objects of this type
        self.AddWindowStyles()

    # This method and the next one are required for XmlResourceHandlers
    def CanHandle(self, node):
        return self.IsOfClass(node, "UnitFloatSlider")

    def DoCreateResource(self):
        assert self.GetInstance() is None

        val = self.GetFloat('value')
        min_val = self.GetFloat('min')
        max_val = self.GetFloat('max')

        text_size = ast.literal_eval(self.GetText('text_size') or "50, -1")

        if min_val == max_val:
            logging.warning("Incorrect range between %r and %r", min_val, max_val)
            max_val = min_val + 1.0

        accuracy = self.GetLong('accuracy', -1)
        if accuracy == -1:
            accuracy = None

        # Now create the object
        slider = slide.UnitFloatSlider(self.GetParentAsWindow(),
                                       wid=self.GetID(),
                                       pos=self.GetPosition(),
                                       size=self.GetSize(),
                                       style=self.GetStyle(),
                                       value=val,
                                       unit=self.GetText('unit'),
                                       min_val=min_val,
                                       max_val=max_val,
                                       scale=self.GetText('scale'),
                                       accuracy=accuracy,
                                       t_size=text_size)

        self.SetupWindow(slider)
        return slider
HANDLER_CLASS_LIST.append(UnitFloatSliderHandler)


class VisualRangeSliderHandler(xrc.XmlResourceHandler):
    def __init__(self):
        xrc.XmlResourceHandler.__init__(self)
        # Specify the styles recognized by objects of this type
        self.AddWindowStyles()

    # This method and the next one are required for XmlResourceHandlers
    def CanHandle(self, node):
        capable = self.IsOfClass(node, "VisualRangeSlider")
        return capable

    def DoCreateResource(self):
        assert self.GetInstance() is None
        # Now create the object
        slider = slide.VisualRangeSlider(self.GetParentAsWindow(),
                                         wid=self.GetID(),
                                         pos=self.GetPosition(),
                                         size=self.GetSize(),
                                         style=self.GetStyle())
        self.SetupWindow(slider)
        slider.SetForegroundColour(slider.GetForegroundColour())
        return slider
HANDLER_CLASS_LIST.append(VisualRangeSliderHandler)


class BandwidthSliderHandler(xrc.XmlResourceHandler):
    def __init__(self):
        xrc.XmlResourceHandler.__init__(self)
        # Specify the styles recognized by objects of this type
        self.AddWindowStyles()

    # This method and the next one are required for XmlResourceHandlers
    def CanHandle(self, node):
        return self.IsOfClass(node, "BandwidthSlider")

    def DoCreateResource(self):
        assert self.GetInstance() is None
        # Now create the object
        slider = slide.BandwidthSlider(self.GetParentAsWindow(),
                                       wid=self.GetID(),
                                       pos=self.GetPosition(),
                                       size=self.GetSize(),
                                       style=self.GetStyle())
        self.SetupWindow(slider)
        # FIXME: this shouldn't be needed, but without it, the content colour is not set
        slider.SetForegroundColour(slider.GetForegroundColour())
        return slider
HANDLER_CLASS_LIST.append(BandwidthSliderHandler)


####################################################################
# OwnerDrawnComboBox Handlers
#
# This handler was needed because the one included with wxPython
# and XRCed did not allow for the alteration of the down button.
####################################################################

class OwnerDrawnComboBoxHandler(xrc.XmlResourceHandler):
    def __init__(self):
        xrc.XmlResourceHandler.__init__(self)
        # Specify the styles recognized by objects of this type
        self.AddStyle("wxCB_SIMPLE", wx.CB_SIMPLE)
        self.AddStyle("wxCB_DROPDOWN", wx.CB_DROPDOWN)
        self.AddStyle("wxCB_READONLY", wx.CB_READONLY)
        self.AddStyle("wxCB_SORT", wx.CB_SORT)
        self.AddStyle("wxODCB_STD_CONTROL_PAINT", wx.adv.ODCB_STD_CONTROL_PAINT)
        self.AddStyle("wxODCB_DCLICK_CYCLES", wx.adv.ODCB_DCLICK_CYCLES)
        self.AddStyle("wxTE_PROCESS_ENTER", wx.TE_PROCESS_ENTER)

        self.AddWindowStyles()

    # This method and the next one are required for XmlResourceHandlers
    def CanHandle(self, node):
        return self.IsOfClass(node, "OwnerDrawnComboBox")

    def DoCreateResource(self):
        assert self.GetInstance() is None

        # Now create the object
        new_ctrl = wx.adv.OwnerDrawnComboBox(self.GetParentAsWindow(),
                                            id=self.GetID(),
                                            pos=self.GetPosition(),
                                            size=self.GetSize(),
                                            style=self.GetStyle())
        new_ctrl.SetButtonBitmaps(img.getBitmap("button/btn_down.png"), pushButtonBg=False)
        self.SetupWindow(new_ctrl)
        return new_ctrl
HANDLER_CLASS_LIST.append(OwnerDrawnComboBoxHandler)


####################################################################
# ToolBar Handler
#
# Small bar for view related tools
####################################################################

class ToolBarHandler(xrc.XmlResourceHandler):

    def __init__(self):
        xrc.XmlResourceHandler.__init__(self)

        self.AddStyle("wxHORIZONTAL", wx.HORIZONTAL)
        self.AddStyle("wxVERTICAL", wx.VERTICAL)

        # Standard styles
        self.AddWindowStyles()
        #self._isInside = False

    def CanHandle(self, node):
        # return not self._isInside and self.IsOfClass(node, 'wx.lib.foldpanelbar.FoldPanelBar') or \
        #        self._isInside and self.IsOfClass(node, 'foldpanel')
        return self.IsOfClass(node, 'ToolBar')

    def DoCreateResource(self):
        assert self.GetInstance() is None

        parent_window = self.GetParentAsWindow()
        # Now create the object
        toolbar = tools.ToolBar(
                        parent_window,
                        self.GetID(),
                        self.GetPosition(),
                        self.GetSize(),
                        style=self.GetStyle(),
                        name=self.GetName(),
                )

        # Set standard window attributes
        self.SetupWindow(toolbar)
        self.CreateChildren(toolbar)
        return toolbar
HANDLER_CLASS_LIST.append(ToolBarHandler)


####################################################################
# ViewportGrid
#
# Container for the 2x2 viewport grid
####################################################################

class ViewportGridHandler(xrc.XmlResourceHandler):

    def __init__(self):
        xrc.XmlResourceHandler.__init__(self)

        # Standard styles
        self.AddWindowStyles()

    def CanHandle(self, node):
        return self.IsOfClass(node, 'ViewportGrid')

    def DoCreateResource(self):
        assert self.GetInstance() is None

        parent_window = self.GetParentAsWindow()
        # Now create the object
        vpgrid = grid.ViewportGrid(
            parent_window,
            self.GetID(),
            self.GetPosition(),
            self.GetSize(),
            style=self.GetStyle(),
            name=self.GetName(),
        )

        # Set standard window attributes
        self.SetupWindow(vpgrid)
        self.CreateChildren(vpgrid)
        return vpgrid
HANDLER_CLASS_LIST.append(ViewportGridHandler)
