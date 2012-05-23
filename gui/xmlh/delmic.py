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

# wx.lib.foldpanelbar.FoldPanelBar

c = component.Container('odemis.gui.comp.foldpanelbar.FoldPanelBar',
    ['window', 'top_level', 'control'],
    ['pos', 'size', 'spacing'])
c.addStyles('FPB_SINGLE_FOLD',
    'FPB_COLLAPSE_TO_BOTTOM',
    'FPB_EXCLUSIVE_FOLD',
    'FPB_HORIZONTAL',
    'FPB_VERTICAL')
c.setParamClass('spacing', params.ParamIntNN)
component.Manager.register(c)
component.Manager.addXmlHandler(xh_delmic.FoldPanelBarXmlHandler)
component.Manager.setMenu(c, 'bar', 'Delmic fold panel bar', 'FoldPanelBar', 10)

c = component.Container('odemis.gui.comp.foldpanelbar.FoldPanelItem',
    ['window', 'top_level', 'control'],
    ['pos', 'size', 'label', 'collapsed'],
    params={'label': params.ParamText, 'collapsed': params.ParamBool})
#c.addStyles('FPB_SINGLE_FOLD', 'FPB_COLLAPSE_TO_BOTTOM',
#            'FPB_EXCLUSIVE_FOLD', 'FPB_HORIZONTAL', 'FPB_VERTICAL')
component.Manager.register(c)
component.Manager.addXmlHandler(xh_delmic.FoldPanelXmlHandler)
component.Manager.setMenu(c, 'TOP_LEVEL', 'Delmic fold panel', 'FoldPanel', 50)
component.Manager.setMenu(c, 'ROOT', 'Delmic fold panel', 'FoldPanel', 20)
#component.Manager.setMenu(c, 'container', 'Delmic fold panel', 'FoldPanel', 10)