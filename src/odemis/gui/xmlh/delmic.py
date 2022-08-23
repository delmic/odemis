# -*- coding: utf-8 -*-
"""
Created: 2012-5-15

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
# XRCED Component plugin for custom Delmic wxPython classes
# Important: Create a symbolic link to this and the xh_delmic module within
#            XRCED's plugins folder.
from collections import OrderedDict

from wx.tools.XRCed import component, params, images, attribute
from wx.tools.XRCed.globals import TRACE

import odemis.gui.xmlh.xh_delmic as xh_delmic

TRACE('*** creating xh_delmic components')

##### FoldPanelBar #####

c = component.Container(
    'FoldPanelBar',
    ['window', 'top_level', 'control'],
    ['pos', 'size', 'spacing', 'leftspacing', 'rightspacing']
)
c.addStyles(
    'FPB_SINGLE_FOLD',
    'FPB_COLLAPSE_TO_BOTTOM',
    'FPB_EXCLUSIVE_FOLD',
    'FPB_HORIZONTAL',
    'FPB_VERTICAL'
)
c.setParamClass('spacing', params.ParamIntNN)
c.setParamClass('leftspacing', params.ParamIntNN)
c.setParamClass('rightspacing', params.ParamIntNN)
component.Manager.register(c)
component.Manager.addXmlHandler(xh_delmic.FoldPanelBarXmlHandler)
component.Manager.setMenu(c, 'Delmic', 'Fold Panel Bar', 'FoldPanelBar', 1)

c = component.Container(
    'FoldPanelItem',
    ['window', 'top_level', 'control'],
    ['pos', 'size', 'label', 'collapsed', 'nocaption'],
    params={
        'label': params.ParamText,
        'collapsed': params.ParamBool,
        'nocaption': params.ParamBool
    }
)
component.Manager.register(c)
component.Manager.addXmlHandler(xh_delmic.FoldPanelItemXmlHandler)
component.Manager.setMenu(c, 'Delmic', 'Fold Panel Item', 'FoldPanelItem', 2)

c = component.Container(
    'CaptionBar',
    ['window', 'top_level', 'control'],
    ['pos', 'size', 'label', 'collapsed'],
    params={'label': params.ParamText, 'collapsed': params.ParamBool}
)
component.Manager.register(c)
component.Manager.addXmlHandler(xh_delmic.CaptionBarXmlHandler)
component.Manager.setMenu(c, 'Delmic', 'Caption Bar', 'CaptionBar', 2)

### StreamBar

c = component.Container(
    'StreamBar',
    ['window', 'top_level', 'control'],
    ['pos', 'size', 'label', 'add_button']
)
c.setParamClass('add_button', params.ParamBool)
component.Manager.register(c)
component.Manager.addXmlHandler(xh_delmic.StreamBarXmlHandler)
component.Manager.setMenu(c, 'Delmic', 'Stream Bar', 'StreamBar', 3)

c = component.Container(
    'StreamPanel',
    ['window', 'top_level', 'control'],
    ['pos', 'size', 'label', 'collapsed']
)
c.setParamClass('collapsed', params.ParamBool)
c.addEvents('EVT_COMMAND_COLLPANE_CHANGED')
component.Manager.register(c)
component.Manager.addXmlHandler(xh_delmic.StreamPanelXmlHandler)
component.Manager.setMenu(c, 'Delmic', 'Generic Stream Entry', 'StreamPanel', 4)


### Stream Bar Fast EM
c = component.Container(
    'StreamBar',
    ['window', 'top_level', 'control'],
    ['pos', 'size', 'label', 'add_button']
)
c.setParamClass('add_button', params.ParamBool)
component.Manager.register(c)
component.Manager.addXmlHandler(xh_delmic.StreamBarXmlHandler)
component.Manager.setMenu(c, 'Delmic', 'Calibration Bar FastEM', 'CalibrationBarFastEM', 3)

c = component.Container(
    'StreamPanel',
    ['window', 'top_level', 'control'],
    ['pos', 'size', 'label', 'collapsed']
)
c.setParamClass('collapsed', params.ParamBool)
c.addEvents('EVT_COMMAND_COLLPANE_CHANGED')
component.Manager.register(c)
component.Manager.addXmlHandler(xh_delmic.StreamPanelXmlHandler)
component.Manager.setMenu(c, 'Delmic', 'Generic Stream Entry', 'StreamPanel', 4)

c = component.Container(
    'StreamBar',
    ['window', 'top_level', 'control'],
    ['pos', 'size', 'label', 'add_button']
)
c.setParamClass('add_button', params.ParamBool)
component.Manager.register(c)
component.Manager.addXmlHandler(xh_delmic.StreamBarXmlHandler)
component.Manager.setMenu(c, 'Delmic', 'Stream Bar FastEM', 'StreamBarFastEM', 3)

c = component.Container(
    'StreamPanel',
    ['window', 'top_level', 'control'],
    ['pos', 'size', 'label', 'collapsed']
)
c.setParamClass('collapsed', params.ParamBool)
c.addEvents('EVT_COMMAND_COLLPANE_CHANGED')
component.Manager.register(c)
component.Manager.addXmlHandler(xh_delmic.StreamPanelXmlHandler)
component.Manager.setMenu(c, 'Delmic', 'Generic Stream Entry', 'StreamPanel', 4)


### gui.comp.viewport.MicroscopeViewport and subclasses

msvps = [
    ('CameraViewport', xh_delmic.CameraViewportXmlHandler),
    ('FixedOverviewViewport', xh_delmic.FixedOverviewViewportXmlHandler),
    ('FeatureOverviewViewport', xh_delmic.FeatureOverviewViewportXmlHandler),
    ('MicroscopeViewport', xh_delmic.MicroscopeViewportXmlHandler),
    ('LiveViewport', xh_delmic.LiveViewportXmlHandler),
    ('ARAcquiViewport', xh_delmic.ARAcquiViewportXmlHandler),
    ('PointSpectrumViewport', xh_delmic.PointSpectrumViewportXmlHandler),
    ('ChronographViewport', xh_delmic.ChronographViewportXmlHandler),
    ('ThetaViewport', xh_delmic.ThetaViewportXmlHandler),
    ('ARLiveViewport', xh_delmic.ARLiveViewportXmlHandler),
    ('EKLiveViewport', xh_delmic.EKLiveViewportXmlHandler),
    ('AngularResolvedViewport', xh_delmic.AngularResolvedViewportXmlHandler),
    ('LineSpectrumViewport', xh_delmic.LineSpectrumViewportXmlHandler),
    ('TemporalSpectrumViewport', xh_delmic.TemporalSpectrumViewportXmlHandler),
    ('AngularSpectrumViewport', xh_delmic.AngularSpectrumViewportXmlHandler),
]

for i, (name, handler) in enumerate(msvps):
    c = component.Container(
        name,
        ['window', 'top_level', 'control'],
        ['pos', 'size'],
        image=images.TreePanel.GetImage()
    )
    c.addStyles('wxTAB_TRAVERSAL')
    component.Manager.register(c)
    component.Manager.addXmlHandler(handler)
    component.Manager.setMenu(c, 'Delmic Viewport', name, name, 10 + i)


class ButtonSizesParam(params.RadioBox):
    choices = OrderedDict(
        (('16', '16'), ('24', '24'), ('32', '32'), ('48', '48'), ('None', None))
    )
    default = None


class ButtonFaceColourParam(params.RadioBox):
    choices = {'Grey': 'def', 'Blue': 'blue', 'Red': 'red', 'Orange': 'orange'}
    default = 'def'

### ImageButton

buttons = [
    ('ImageButton', xh_delmic.ImageButtonHandler,
     'New button', 'Icon Button', False),
    ('ImageToggleButtonImageButton', xh_delmic.ImageToggleButtonHandler,
     'New button', 'Icon Toggle Button', False),
    ('ImageTextButton', xh_delmic.ImageTextButtonHandler,
     'New button', 'Icon Text Button', True),
    ('ImageTextToggleButton', xh_delmic.ImageTextToggleButtonHandler,
     'New button', 'Icon Text Toggle Button', True),
    ('GraphicRadioButton', xh_delmic.GraphicRadioButtonHandler,
     'New button', 'Icon Text Toggle Group Button', True),
    ('TabButton', xh_delmic.TabButtonHandler,
     'New button', 'Tab Button', True),
    ('ViewButton', xh_delmic.ViewButtonHandler,
     'New button', 'View Button', True),
]

for btn in buttons:
    attrs = ['pos', 'size', 'icon', 'icon_on', 'height', 'face_colour']
    attrs += ['label'] if btn[4] else []

    c = component.Component(
        btn[0], ['control', 'tool'],
        attrs,
        image=images.TreeBitmapButton.GetImage()
    )
    c.addStyles('wxALIGN_LEFT', 'wxALIGN_RIGHT', 'wxALIGN_CENTRE')
    c.setSpecial('icon',  attribute.BitmapAttribute)
    c.setParamClass('icon', params.ParamBitmap)
    c.setSpecial('icon_on',  attribute.BitmapAttribute)
    c.setParamClass('icon_on', params.ParamBitmap)
    c.setParamClass('height', ButtonSizesParam)
    c.setParamClass('face_colour', ButtonFaceColourParam)

    c.addEvents('EVT_BUTTON')
    component.Manager.register(c)
    component.Manager.addXmlHandler(btn[1])
    component.Manager.setMenu(c, btn[2], btn[3], btn[0], 2)
    component.Manager.setTool(c, 'Controls', pos=(1, 1))

### SuggestTextCtrl

c = component.Component(
    'SuggestTextCtrl',
    ['control', 'tool'],
    ['pos', 'size', 'value', 'maxlength'],
    image=images.TreeTextCtrl.GetImage()
)
c.addStyles(
    'wxTE_NO_VSCROLL',
    'wxTE_AUTO_SCROLL',
    'wxTE_PROCESS_ENTER',
    'wxTE_PROCESS_TAB',
    'wxTE_MULTILINE',
    'wxTE_PASSWORD',
    'wxTE_READONLY',
    'wxHSCROLL',
    'wxTE_RICH',
    'wxTE_RICH2',
    'wxTE_AUTO_URL',
    'wxTE_NOHIDESEL',
    'wxTE_LEFT',
    'wxTE_CENTRE',
    'wxTE_RIGHT',
    'wxTE_DONTWRAP',
    'wxTE_LINEWRAP',
    'wxTE_CHARWRAP',
    'wxTE_WORDWRAP'
)
c.setParamClass('value', params.ParamMultilineText)
c.addEvents('EVT_TEXT', 'EVT_TEXT_ENTER', 'EVT_TEXT_URL', 'EVT_TEXT_MAXLEN')
component.Manager.register(c)
component.Manager.addXmlHandler(xh_delmic.SuggestTextCtrlHandler)
component.Manager.setMenu(
    c,
    'Delmic control',
    'Suggest Text',
    'SuggestTextCtrl',
    1
)
component.Manager.setTool(c, 'Controls', pos=(0, 2))


### UnitIntegerCtrl

c = component.Component(
    'UnitIntegerCtrl',
    ['control', 'tool'],
    ['pos', 'size', 'value', 'min', 'max', 'unit'],
    image=images.TreeTextCtrl.GetImage()
)
c.addStyles(
    'wxTE_NO_VSCROLL',
    'wxTE_AUTO_SCROLL',
    'wxTE_PROCESS_ENTER',
    'wxTE_PROCESS_TAB',
    'wxTE_MULTILINE',
    'wxTE_PASSWORD',
    'wxTE_READONLY',
    'wxHSCROLL',
    'wxTE_RICH',
    'wxTE_RICH2',
    'wxTE_AUTO_URL',
    'wxTE_NOHIDESEL',
    'wxTE_LEFT',
    'wxTE_CENTRE',
    'wxTE_RIGHT',
    'wxTE_DONTWRAP',
    'wxTE_LINEWRAP',
    'wxTE_CHARWRAP',
    'wxTE_WORDWRAP'
)
c.setParamClass('value', params.ParamMultilineText)
c.setParamClass('min', params.ParamInt)
c.setParamClass('max', params.ParamInt)
c.setParamClass('unit', params.MetaParamText(10))
c.addEvents('EVT_TEXT', 'EVT_TEXT_ENTER', 'EVT_TEXT_URL', 'EVT_TEXT_MAXLEN')
component.Manager.register(c)
component.Manager.addXmlHandler(xh_delmic.UnitIntegerCtrlHandler)
component.Manager.setMenu(
    c,
    'Delmic control',
    'Integer Text',
    'UnitIntegerCtrl',
    1
)
component.Manager.setTool(c, 'Controls', pos=(0, 2))

### UnitFloatCtrl

c = component.Component(
    'UnitFloatCtrl',
    ['control', 'tool'],
    ['pos', 'size', 'value', 'min', 'max', 'unit'],
    image=images.TreeTextCtrl.GetImage()
)
c.addStyles(
    'wxTE_NO_VSCROLL',
    'wxTE_AUTO_SCROLL',
    'wxTE_PROCESS_ENTER',
    'wxTE_PROCESS_TAB',
    'wxTE_MULTILINE',
    'wxTE_PASSWORD',
    'wxTE_READONLY',
    'wxHSCROLL',
    'wxTE_RICH',
    'wxTE_RICH2',
    'wxTE_AUTO_URL',
    'wxTE_NOHIDESEL',
    'wxTE_LEFT',
    'wxTE_CENTRE',
    'wxTE_RIGHT',
    'wxTE_DONTWRAP',
    'wxTE_LINEWRAP',
    'wxTE_CHARWRAP',
    'wxTE_WORDWRAP'
)
# Note: there is no ParamFloat class
c.setParamClass('value', params.ParamMultilineText)
c.setParamClass('min', params.ParamInt)
c.setParamClass('max', params.ParamInt)
c.setParamClass('unit', params.MetaParamText(80))
c.addEvents('EVT_TEXT', 'EVT_TEXT_ENTER', 'EVT_TEXT_URL', 'EVT_TEXT_MAXLEN')
component.Manager.register(c)
component.Manager.addXmlHandler(xh_delmic.UnitFloatCtrlHandler)
component.Manager.setMenu(c, 'Delmic control', 'Float Text', 'UnitFloatCtrl', 1)
component.Manager.setTool(c, 'Controls', pos=(0, 2))


### UnitFloatSlider

class ParamScale(params.RadioBox):
    choices = {'Linear': 'linear', 'Cubic': 'cubic', 'Log': 'log'}
    default = 'linear'

c = component.Component(
    'UnitFloatSlider',
    ['control', 'tool'],
    ['pos', 'size', 'value', 'min', 'max', 'unit', 'scale', 'text_size', 'accuracy'],
    image=images.TreeTextCtrl.GetImage()
)
c.setParamClass('value', params.ParamText)
c.setParamClass('min', params.ParamInt)
c.setParamClass('max', params.ParamInt)
c.setParamClass('unit', params.MetaParamText(80))
c.setParamClass('scale', ParamScale)
c.setParamClass('accuracy', params.ParamPosSize)
c.setParamClass('text_size', params.ParamPosSize)

component.Manager.register(c)
component.Manager.addXmlHandler(xh_delmic.UnitFloatSliderHandler)
component.Manager.setMenu(
    c,
    'Delmic control',
    'Float Slider',
    'UnitFloatSlider',
    1
)
component.Manager.setTool(c, 'Controls', pos=(0, 2))


### UnitIntegerSlider

c = component.Component(
    'UnitIntegerSlider',
    ['control', 'tool'],
    ['pos', 'size', 'value', 'min', 'max', 'unit', 'scale', 'text_size'],
    image=images.TreeTextCtrl.GetImage()
)
c.setParamClass('value', params.ParamText)
c.setParamClass('min', params.ParamInt)
c.setParamClass('max', params.ParamInt)
c.setParamClass('unit', params.MetaParamText(80))
c.setParamClass('scale', ParamScale)
c.setParamClass('text_size', params.ParamPosSize)

component.Manager.register(c)
component.Manager.addXmlHandler(xh_delmic.UnitIntegerSliderHandler)
component.Manager.setMenu(
    c,
    'Delmic control',
    'Integer Slider',
    'UnitIntegerSlider',
    1
)
component.Manager.setTool(c, 'Controls', pos=(0, 2))


### VisualRangeSlider

c = component.Component(
    'VisualRangeSlider',
    ['control', 'tool'],
    ['pos', 'size'],
    image=images.TreeTextCtrl.GetImage()
)
component.Manager.register(c)
component.Manager.addXmlHandler(xh_delmic.VisualRangeSliderHandler)
component.Manager.setMenu(
    c,
    'Delmic control',
    'Visual Range Slider',
    'VisualRangeSlider',
    1
)
component.Manager.setTool(c, 'Controls', pos=(0, 2))

### BandwidthSlider

c = component.Component(
    'BandwidthSlider',
    ['control', 'tool'],
    ['pos', 'size'],
    image=images.TreeTextCtrl.GetImage()
)
component.Manager.register(c)
component.Manager.addXmlHandler(xh_delmic.BandwidthSliderHandler)
component.Manager.setMenu(
    c,
    'Delmic control',
    'BandwidthSlider Slider',
    'BandwidthSlider',
    1
)
component.Manager.setTool(c, 'Controls', pos=(0, 2))

### wxOwnerDrawnComboBox
# This component is present in the default XRCed setup, but was added here
# because the original did not allow for the setting of the down button
# image. (Which we implemented in the xh_delmic module in this package)

c = component.Component(
    'OwnerDrawnComboBox',
    ['control', 'tool'],
    ['pos', 'size'],
    image=images.TreeComboBox.GetImage()
)
c.addStyles(
    'wxCB_SIMPLE',
    'wxCB_DROPDOWN',
    'wxCB_READONLY',
    'wxCB_SORT',
    'wxODCB_STD_CONTROL_PAINT',
    'wxODCB_DCLICK_CYCLES',
    'wxTE_PROCESS_ENTER'
)
c.setSpecial('content', attribute.ContentAttribute)
c.addEvents('EVT_COMBOBOX', 'EVT_TEXT', 'EVT_TEXT_ENTER')
component.Manager.register(c)
component.Manager.addXmlHandler(xh_delmic.OwnerDrawnComboBoxHandler)
component.Manager.setMenu(
    c,
    'Delmic control',
    'Owner-Drawn Combo Box',
    'OwnerDrawnComboBox',
    21
)

### ToolBar

c = component.Component(
    'ToolBar',
    ['window', 'top_level', 'control'],
    ['pos', 'size'],
    image=images.TreeMenuBar.GetImage()
)
c.addStyles(
    'wxHORIZONTAL',
    'wxVERTICAL'
)

component.Manager.register(c)
component.Manager.addXmlHandler(xh_delmic.ToolBarHandler)
component.Manager.setMenu(c, 'Delmic', 'Tool Bar', 'ToolBar', 32)

### ViewportGrid

c = component.Container(
    'ViewportGrid',
    ['window', 'top_level', 'control'],
    ['pos', 'size'],
    image=images.TreePanel.GetImage()
)

c.addStyles('wxTAB_TRAVERSAL')
component.Manager.register(c)
component.Manager.setMenu(c, 'Delmic', 'Grid Container', 'GridContainer', 30)
component.Manager.addXmlHandler(xh_delmic.ViewportGridHandler)
component.Manager.setTool(c, 'Controls', pos=(0, 2))
