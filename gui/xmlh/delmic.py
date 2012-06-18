# Name:         delmic.py
# Purpose:      XRCED Component plugin for custom Delmic wxPython classes
# Author:       R. de Laat
# Created:      2012-5-15
#
# Create a symbolic link to this and the xh_delmic module within XRCED's
# plugins folder.

import xh_delmic
from wx.tools.XRCed import component, params
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