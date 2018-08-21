#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Embed a XRC file into a Python file.
# python ~/alien/Phoenix/wx/tools/pywxrc.py -p -e -o src/odemis/gui/main_xrc.py.new src/odemis/gui/main.xrc

from __future__ import division, print_function, absolute_import

import glob
import os
import re
import sys
from wx.tools.pywxrc import PythonTemplates, XmlResourceCompiler

# Compatible with both wxPython3 and 4
PythonTemplates.CLASS_HEADER = """\
class xrc%(windowName)s(wx.%(windowClass)s):
#!XRCED:begin-block:xrc%(windowName)s.PreCreate
    def PreCreate(self, *args):
        \"\"\" This function is called during the class's initialization.

        Override it for custom setup before the window is created usually to
        set additional window styles using SetWindowStyle() and SetExtraStyle().
        \"\"\"
        pass

#!XRCED:end-block:xrc%(windowName)s.PreCreate

    def __init__(self, parent):
        if wx.MAJOR_VERSION == 3:
            # Two stage creation (see http://wiki.wxpython.org/index.cgi/TwoStageCreation)
            pre = wx.Pre%(windowClass)s()
            self.PreCreate(pre)
            get_resources().LoadOn%(windowClass)s(pre, parent, "%(windowName)s")
            self.PostCreate(pre)
        else:
            wx.%(windowClass)s.__init__(self)
            self.PreCreate()
            get_resources().Load%(windowClass)s(self, parent, "%(windowName)s")

        # Define variables for the controls, bind event handlers
"""

# Fix bytearray (on Python2?)
PythonTemplates.ADD_FILE_TO_MEMFS = """\
    wx.MemoryFSHandler.AddFile('XRC/%(memoryPath)s/%(filename)s', bytearray(%(filename)s))
"""


class OdemisXmlResourceCompiler(XmlResourceCompiler):

    def NodeContainsFilename(self, node):
        """ Does 'node' contain filename information at all? """

        if node.nodeName == "icon_on":
            return True
            
        return XmlResourceCompiler.NodeContainsFilename(self, node)

    # Fixed version, for label of the menu
    def GenerateWidgetClass(self, windowClass, windowName, topWindow, vars):
        outputList = []

        # output the header
        outputList.append(self.templates.CLASS_HEADER % locals())

        # Generate an attribute for each named item in the container
        for widget in topWindow.getElementsByTagName("object"):
            if not self.CheckAssignVar(widget): continue
            widgetClass = widget.getAttribute("class")
            widgetClass = re.sub("^wx", "", widgetClass)
            widgetName = widget.getAttribute("name")
            if widgetName != "" and widgetClass != "":
                vars.append(widgetName)
                if widgetClass == "MenuBar":
                    outputList.append(self.templates.FRAME_MENUBAR_VAR % locals())
                elif widgetClass == "MenuItem":
                    outputList.append(self.templates.FRAME_MENUBAR_MENUITEM_VAR % locals())
                elif widgetClass == "Menu":
                    # Only look directly under for the "label"
                    for e in widget.childNodes:
                        if e.nodeType == e.ELEMENT_NODE and e.tagName == "label":
                            label = e
                            break
                    label = label.childNodes[0].data
                    outputList.append(self.templates.FRAME_MENUBAR_MENU_VAR % locals())
#                 elif widgetClass == "ToolBar":
#                     outputList.append(self.templates.FRAME_TOOLBAR_VAR % locals())
                elif widgetClass == "tool":
                    outputList.append(self.templates.FRAME_TOOLBAR_TOOL_VAR % locals())
                elif widgetClass in ('unknown', 'notebookpage', 'separator', 'sizeritem'):
                    pass
                else:
                    outputList.append(self.templates.CREATE_WIDGET_VAR % locals())

        return outputList


def main(args=None):
    if not args:
        args = sys.argv[1:]

    inputFiles = []
    for arg in args:
        inputFiles += glob.glob(arg)

    embedResources = True
    generateGetText = False
    assignVariables = True
    
    comp = OdemisXmlResourceCompiler()

    try:
        outputFilename = os.path.splitext(args[0])[0] + "_xrc.py"
        comp.MakePythonModule(inputFiles, outputFilename,
                              embedResources, generateGetText,
                              assignVariables)
    except IOError as exc:
        print("%s." % str(exc), file=sys.stderr)
    else:
        print("Resources written to %s." % outputFilename, file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1:])
