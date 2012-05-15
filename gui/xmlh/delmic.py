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

c = component.SmartContainer('odemis.gui.comp.foldpanelbar.FoldPanelBar', ['book', 'window', 'control'],
                   ['pos', 'size'],
                   implicit_klass='foldpanel',
                   implicit_page='FoldPanel',
                   implicit_attributes=['label', 'collapsed'],
                   implicit_params={'collapsed': params.ParamBool})
c.addStyles('FPB_SINGLE_FOLD', 'FPB_COLLAPSE_TO_BOTTOM',
            'FPB_EXCLUSIVE_FOLD', 'FPB_HORIZONTAL', 'FPB_VERTICAL')
component.Manager.register(c)
component.Manager.addXmlHandler(xh_delmic.FoldPanelBarXmlHandler)
component.Manager.setMenu(c, 'bar', 'Delmic fold panel bar', 'FoldPanelBar', 1000)

