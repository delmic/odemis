# Name:         delmic.py
# Purpose:      XRCED Component plugin for custom Delmic wxPython classes
# Author:       R. de Laat
# Created:      2012-5-15
#
# Create a symbolic link to this and the xh_delmic module within XRCED's
# plugins folder.

import xh_delmic
from wx.tools.XRCed import component, params, images, attribute
from wx.tools.XRCed.globals import TRACE

TRACE('*** creating xh_delmic components')

### odemis.gui.comp.stream.StreamPanel

c = component.Container('odemis.gui.comp.stream.FixedStreamPanel',
    ['window', 'top_level', 'control'],
    ['pos', 'size', 'label', 'collapsed'])
c.setParamClass('collapsed', params.ParamBool)
c.addEvents('EVT_COMMAND_COLLPANE_CHANGED')
component.Manager.register(c)
component.Manager.addXmlHandler(xh_delmic.FixedStreamPanelXmlHandler)
component.Manager.setMenu(c, 'TOP_LEVEL', 'Delmic fixed stream panel', 'FixedStreamPanel', 3)
component.Manager.setMenu(c, 'ROOT', 'Delmic fixed stream panel', 'FixedStreamPanel', 3)

c = component.Container('odemis.gui.comp.stream.CustomStreamPanel',
    ['window', 'top_level', 'control'],
    ['pos', 'size', 'label', 'collapsed'])
c.setParamClass('collapsed', params.ParamBool)
c.addEvents('EVT_COMMAND_COLLPANE_CHANGED')
component.Manager.register(c)
component.Manager.addXmlHandler(xh_delmic.CustomStreamPanelXmlHandler)
component.Manager.setMenu(c, 'TOP_LEVEL', 'Delmic custom stream panel', 'CustomStreamPanel', 4)
component.Manager.setMenu(c, 'ROOT', 'Delmic custom stream panel', 'CustomStreamPanel', 4)


##### odemis.gui.comp.foldpanelbar.FoldPanelBar #####

c = component.Container('odemis.gui.comp.foldpanelbar.FoldPanelBar',
    ['window', 'top_level', 'control'],
    ['pos', 'size', 'spacing', 'leftspacing', 'rightspacing'])
c.addStyles('FPB_SINGLE_FOLD',
    'FPB_COLLAPSE_TO_BOTTOM',
    'FPB_EXCLUSIVE_FOLD',
    'FPB_HORIZONTAL',
    'FPB_VERTICAL')
c.setParamClass('spacing', params.ParamIntNN)
c.setParamClass('leftspacing', params.ParamIntNN)
c.setParamClass('rightspacing', params.ParamIntNN)
component.Manager.register(c)
component.Manager.addXmlHandler(xh_delmic.FoldPanelBarXmlHandler)
component.Manager.setMenu(c, 'bar', 'Delmic fold panel bar', 'FoldPanelBar', 1)

c = component.Container('odemis.gui.comp.foldpanelbar.FoldPanelItem',
    ['window', 'top_level', 'control'],
    ['pos', 'size', 'label', 'collapsed'],
    params={'label': params.ParamText, 'collapsed': params.ParamBool})
#c.addStyles('FPB_SINGLE_FOLD', 'FPB_COLLAPSE_TO_BOTTOM',
#            'FPB_EXCLUSIVE_FOLD', 'FPB_HORIZONTAL', 'FPB_VERTICAL')
component.Manager.register(c)
component.Manager.addXmlHandler(xh_delmic.FoldPanelXmlHandler)
component.Manager.setMenu(c, 'TOP_LEVEL', 'Delmic fold panel', 'FoldPanel', 2)
component.Manager.setMenu(c, 'ROOT', 'Delmic fold panel', 'FoldPanel', 2)
#component.Manager.setMenu(c, 'container', 'Delmic fold panel', 'FoldPanel', 10)


### wx.lib.buttons.GenBitmapButton

c = component.Component('wx.lib.buttons.GenBitmapButton', ['control', 'tool'],
              ['pos', 'size', 'default',
               'bitmap', 'selected', 'focus', 'disabled'],
              image=images.TreeBitmapButton.GetImage())
#c.addStyles()
c.setParamClass('default', params.ParamBool)
c.setSpecial('bitmap',  attribute.BitmapAttribute)

c.setSpecial('selected',  attribute.BitmapAttribute)
c.setParamClass('selected', params.ParamBitmap)

c.setSpecial('focus',  attribute.BitmapAttribute)
c.setParamClass('focus', params.ParamBitmap)

c.setSpecial('disabled',  attribute.BitmapAttribute)
c.setParamClass('disabled', params.ParamBitmap)


c.addEvents('EVT_BUTTON')
component.Manager.register(c)
component.Manager.addXmlHandler(xh_delmic.GenBitmapButtonHandler)
component.Manager.setMenu(c, 'button', 'generic bitmap button', 'wx.lib.buttons.GenBitmapButton', 20)
component.Manager.setTool(c, 'Controls', pos=(1, 1))


### odemis.gui.comp.buttons.ImageButton

c = component.Component('odemis.gui.comp.buttons.ImageButton', ['control', 'tool'],
              ['pos', 'size', 'default',
               'bitmap', 'hover', 'selected', 'focus', 'disabled'],
              image=images.TreeBitmapButton.GetImage())
#c.addStyles()
c.setParamClass('default', params.ParamBool)
c.setSpecial('bitmap',  attribute.BitmapAttribute)

c.setSpecial('hover',  attribute.BitmapAttribute)
c.setParamClass('hover', params.ParamBitmap)

c.setSpecial('selected',  attribute.BitmapAttribute)
c.setParamClass('selected', params.ParamBitmap)

c.setSpecial('focus',  attribute.BitmapAttribute)
c.setParamClass('focus', params.ParamBitmap)

c.setSpecial('disabled',  attribute.BitmapAttribute)
c.setParamClass('disabled', params.ParamBitmap)


c.addEvents('EVT_BUTTON')
component.Manager.register(c)
component.Manager.addXmlHandler(xh_delmic.ImageButtonHandler)
component.Manager.setMenu(c, 'button', 'Delmic hover bitmap button', 'odemis.gui.comp.buttons.ImageButton', 20)
component.Manager.setTool(c, 'Controls', pos=(1, 1))
