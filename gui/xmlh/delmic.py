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

### StreamPanel

c = component.Container('FixedStreamPanelEntry',
    ['window', 'top_level', 'control'],
    ['pos', 'size', 'label', 'collapsed'])
c.setParamClass('collapsed', params.ParamBool)
c.addEvents('EVT_COMMAND_COLLPANE_CHANGED')
component.Manager.register(c)
component.Manager.addXmlHandler(xh_delmic.FixedStreamPanelEntryXmlHandler)
component.Manager.setMenu(c, 'TOP_LEVEL', 'Delmic fixed stream entry', 'FixedStreamPanelEntry', 3)
component.Manager.setMenu(c, 'ROOT', 'Delmic fixed stream panel', 'FixedStreamPanel', 3)

c = component.Container('CustomStreamPanelEntry',
    ['window', 'top_level', 'control'],
    ['pos', 'size', 'label', 'collapsed'])
c.setParamClass('collapsed', params.ParamBool)
c.addEvents('EVT_COMMAND_COLLPANE_CHANGED')
component.Manager.register(c)
component.Manager.addXmlHandler(xh_delmic.CustomStreamPanelEntryXmlHandler)
component.Manager.setMenu(c, 'TOP_LEVEL', 'Delmic custom stream entry', 'CustomStreamPanelEntry', 4)
component.Manager.setMenu(c, 'ROOT', 'Delmic custom stream panel', 'CustomStreamPanel', 4)


##### FoldPanelBar #####

c = component.Container('FoldPanelBar',
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
component.Manager.setMenu(c, 'bar', 'Delmic fold bar', 'FoldPanelBar', 1)


c = component.Container('FoldPanelItem',
    ['window', 'top_level', 'control'],
    ['pos', 'size', 'label', 'collapsed'],
    params={'label': params.ParamText, 'collapsed': params.ParamBool})
#c.addStyles('FPB_SINGLE_FOLD', 'FPB_COLLAPSE_TO_BOTTOM',
#            'FPB_EXCLUSIVE_FOLD', 'FPB_HORIZONTAL', 'FPB_VERTICAL')
component.Manager.register(c)
component.Manager.addXmlHandler(xh_delmic.FoldPanelItemXmlHandler)
component.Manager.setMenu(c, 'TOP_LEVEL', 'Delmic fold panel', 'FoldPanelItem', 2)
component.Manager.setMenu(c, 'ROOT', 'Delmic fold panel', 'FoldPanelItem', 2)
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


### ImageButton

c = component.Component('ImageButton', ['control', 'tool'],
              ['pos', 'size', 'default', 'delta',
               'bitmap', 'hover', 'selected', 'focus', 'disabled'],
              image=images.TreeBitmapButton.GetImage())
#c.addStyles()
c.setParamClass('delta', params.ParamInt)

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
component.Manager.setMenu(c, 'button', 'Delmic hover bitmap button', 'ImageButton', 20)
component.Manager.setTool(c, 'Controls', pos=(1, 1))


### ImageTextButton

c = component.Component('ImageTextButton', ['control', 'tool'],
              ['pos', 'size', 'default', 'label', 'delta',
               'bitmap', 'hover', 'selected', 'focus', 'disabled'],
              image=images.TreeBitmapButton.GetImage())
c.addStyles('wxALIGN_LEFT', 'wxALIGN_RIGHT', 'wxALIGN_CENTRE')

c.setParamClass('delta', params.ParamInt)

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
component.Manager.addXmlHandler(xh_delmic.ImageTextButtonHandler)
component.Manager.setMenu(c, 'button', 'Delmic hover bitmap text button', 'ImageTextButton', 20)
component.Manager.setTool(c, 'Controls', pos=(1, 1))

### ImageTextToggleButton

c = component.Component('ImageTextToggleButton', ['control', 'tool'],
              ['pos', 'size', 'default', 'label', 'delta',
               'bitmap', 'hover', 'selected', 'focus', 'disabled'],
              image=images.TreeBitmapButton.GetImage())
c.addStyles('wxALIGN_LEFT', 'wxALIGN_RIGHT', 'wxALIGN_CENTRE')

c.setParamClass('delta', params.ParamInt)

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
component.Manager.addXmlHandler(xh_delmic.ImageTextToggleButtonHandler)
component.Manager.setMenu(c, 'button', 'Delmic hover bitmap text toggle button', 'ImageTextToggleButton', 20)
component.Manager.setTool(c, 'Controls', pos=(1, 1))

### ImageTextTabButton

c = component.Component('ImageTextTabButton', ['control', 'tool'],
              ['pos', 'size', 'default', 'label', 'delta',
               'bitmap', 'hover', 'selected', 'focus', 'disabled'],
              image=images.TreeBitmapButton.GetImage())
c.addStyles('wxALIGN_LEFT', 'wxALIGN_RIGHT', 'wxALIGN_CENTRE')

c.setParamClass('delta', params.ParamInt)

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
component.Manager.addXmlHandler(xh_delmic.ImageTextTabButtonButtonHandler)
component.Manager.setMenu(c, 'button', 'Delmic tab button', 'ImageTextTabButton', 20)
component.Manager.setTool(c, 'Controls', pos=(1, 1))

### PopupImageButton

c = component.Component('PopupImageButton', ['control', 'tool'],
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
component.Manager.addXmlHandler(xh_delmic.PopupImageButtonHandler)
component.Manager.setMenu(c, 'button', 'Delmic popup bitmap button', 'PopupImageButton', 20)
component.Manager.setTool(c, 'Controls', pos=(1, 1))


### SuggestTextCtrl

c = component.Component('SuggestTextCtrl', ['control','tool'],
              ['pos', 'size', 'value', 'maxlength'],
              image=images.TreeTextCtrl.GetImage())
c.addStyles('wxTE_NO_VSCROLL',
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
            'wxTE_WORDWRAP')
c.setParamClass('value', params.ParamMultilineText)
c.addEvents('EVT_TEXT', 'EVT_TEXT_ENTER', 'EVT_TEXT_URL', 'EVT_TEXT_MAXLEN')
component.Manager.register(c)
component.Manager.addXmlHandler(xh_delmic.SuggestTextCtrlHandler)
component.Manager.setMenu(c, 'control', 'Delmic suggest text ctrl', 'SuggestTextCtrl', 1)
component.Manager.setTool(c, 'Controls', pos=(0,2))


### UnitIntegerCtrl

c = component.Component('UnitIntegerCtrl', ['control','tool'],
              ['pos', 'size', 'value', 'min', 'max', 'unit'],
              image=images.TreeTextCtrl.GetImage())
c.addStyles('wxTE_NO_VSCROLL',
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
            'wxTE_WORDWRAP')
c.setParamClass('value', params.ParamMultilineText)
c.setParamClass('min', params.ParamInt)
c.setParamClass('max', params.ParamInt)
c.setParamClass('unit', params.MetaParamText(80))
c.addEvents('EVT_TEXT', 'EVT_TEXT_ENTER', 'EVT_TEXT_URL', 'EVT_TEXT_MAXLEN')
component.Manager.register(c)
component.Manager.addXmlHandler(xh_delmic.UnitIntegerCtrlHandler)
component.Manager.setMenu(c, 'control', 'Delmic unit integer text ctrl', 'UnitIntegerCtrl', 1)
component.Manager.setTool(c, 'Controls', pos=(0, 2))


### odemis.gui.dblmscopepanel.DblMicroscopePanel

c = component.Container('DblMicroscopePanel', ['window', 'top_level', 'control'],
              ['pos', 'size'],
              image=images.TreePanel.GetImage())
c.addStyles('wxTAB_TRAVERSAL')
component.Manager.register(c)
component.Manager.addXmlHandler(xh_delmic.DblMicroscopePanelXmlHandler)
component.Manager.setMenu(c, 'ROOT', 'Delmic Micros Panel', 'DblMicroscopePanel', 10)
