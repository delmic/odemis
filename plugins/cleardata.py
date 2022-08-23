# -*- coding: utf-8 -*-
'''
Created on 13 Jun 2016

@author: Éric Piel

Add a menu entry to remove the currently loaded file in Analysis tab without
loading another one. Mostly for debugging.

This is free and unencumbered software released into the public domain.

Anyone is free to copy, modify, publish, use, compile, sell, or
distribute this software, either in source code form or as a compiled
binary, for any purpose, commercial or non-commercial, and by any
means.

In jurisdictions that recognize copyright laws, the author or authors
of this software dedicate any and all copyright interest in the
software to the public domain. We make this dedication for the benefit
of the public at large and to the detriment of our heirs and
successors. We intend this dedication to be an overt act of
relinquishment in perpetuity of all present and future rights to this
software under copyright law.

The software is provided "as is", without warranty of any kind,
express or implied, including but not limited to the warranties of
merchantability, fitness for a particular purpose and non-infringement.
In no event shall the authors be liable for any claim, damages or
other liability, whether in an action of contract, tort or otherwise,
arising from, out of or in connection with the software or the use or
other dealings in the software.
'''

from odemis.gui.plugin import Plugin


class ClearPlugin(Plugin):
    name = "Clear Data"
    __version__ = "1.0"
    __author__ = u"Éric Piel"
    __license__ = "Public domain"

    def __init__(self, microscope, main_app):
        super(ClearPlugin, self).__init__(microscope, main_app)
        self.addMenu("Help/Development/Clear data", self.clear)

    def clear(self):
        analysis_tab = self.main_app.main_data.getTabByName('analysis')
        analysis_tab.display_new_data(None, None)
