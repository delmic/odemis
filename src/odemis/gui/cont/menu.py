# -*- coding: utf-8 -*-
'''
Created on 2 Jul 2014

@author: Éric Piel

Copyright © 2014 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
import odemis.gui.conf
from odemis.gui.model.dye import DyeDatabase
from odemis.gui.util import call_after
import subprocess
import wx

import odemis.gui.img.data as imgdata


# Menu controller:
class MenuController(object):
    """ This controller handles (some of) the menu actions.
    Some other actions are directly handled by the main class or the specific
    tab controller.
    """

    def __init__(self, main_data, main_frame):
        """ Binds the menu actions.

        main_data (MainGUIData): the representation of the microscope GUI
        main_frame: (wx.Frame): the main frame of the GUI
        """
        self._main_data = main_data
        self._main_frame = main_frame

        # /File
        # /File/Open...
        wx.EVT_MENU(main_frame,
                    main_frame.menu_item_open.GetId(),
                    self._on_open)

        # /File/Save (as), is handled by the snapshot controller

        # TODO: Display "Esc" as accelerator in the menu (wxPython doesn't
        # seem to like it). For now [ESC] is mentioned in the menu item text
        wx.EVT_MENU(main_frame,
                    main_frame.menu_item_halt.GetId(),
                    self.on_stop_axes)

        # The escape accelerator has to be added manually, because for some
        # reason, the 'ESC' key will not register using XRCED.
        accel_tbl = wx.AcceleratorTable([
            (wx.ACCEL_NORMAL, wx.WXK_ESCAPE,
             main_frame.menu_item_halt.GetId())
        ])

        main_frame.SetAcceleratorTable(accel_tbl)

        # /File/Quit is handled by main

        # /View

        # /View/2x2 is handled by the tab controllers
        # /View/cross hair is handled by the tab controllers

        # TODO: Assign 'Play Stream' functionality
        # wx.EVT_MENU(self.main_frame,
        #             self.main_frame.menu_item_play_stream.GetId(),
        #             <function>)

        # TODO: Assign 'Auto Brightness/Contrast' functionality
        # wx.EVT_MENU(self.main_frame,
        #             self.main_frame.menu_item_cont.GetId(),
        #             <function>)

        # TODO: Assign 'Auto Focus' functionality
        # wx.EVT_MENU(self.main_frame,
        #             self.main_frame.menu_auto_focus.GetId(),
        #             <function>)

        # TODO: Assign 'Reset fine alignment' functionality
        # wx.EVT_MENU(self.main_frame,
        #             self.main_frame.menu_item_reset_finealign.GetId(),
        #             <function>)

        # TODO: add fit to view (cf toolbar)

        # /Help
        gc = odemis.gui.conf.get_general_conf()

        # /Help/Manual
        wx.EVT_MENU(main_frame,
                    main_frame.menu_item_manual.GetId(),
                    self._on_manual)
        if gc.get_manual(main_data.role):
            main_frame.menu_item_manual.Enable(True)

        # /Help/Development/Manual
        wx.EVT_MENU(main_frame,
                    main_frame.menu_item_devmanual.GetId(),
                    self._on_dev_manual)
        if gc.get_dev_manual():
            main_frame.menu_item_devmanual.Enable(True)

        # /Help/Development/Inspect GUI
        wx.EVT_MENU(main_frame,
                    main_frame.menu_item_inspect.GetId(),
                    self._on_inspect)

        # /Help/Development/Debug
        wx.EVT_MENU(main_frame,
                    main_frame.menu_item_debug.GetId(),
                    self._on_debug_menu)
        main_data.debug.subscribe(self._on_debug_va, init=True)

        # TODO: Assign 'Bug report' functionality
        # * Report a bug... (Opens a mail client to send an email to us?)
        # wx.EVT_MENU(self.main_frame,
        #             self.main_frame.menu_bugreport.GetId(),
        #             <function>)

        # /Help/About
        wx.EVT_MENU(main_frame,
                    main_frame.menu_item_about.GetId(),
                    self._on_about)

    def on_stop_axes(self, evt):
        if self._main_data:
            self._main_data.stopMotion()
        else:
            evt.Skip()

    def _on_open(self, evt):
        """
        Same as "select image..." of the gallery, but automatically switch to the
        gallery tab (if a file is selected)
        """
        analysis_tab = self._main_data.getTabByName("analysis")
        if analysis_tab.select_acq_file():
            # show the new tab
            self._main_data.tab.value = analysis_tab

    def _on_about(self, evt):

        info = wx.AboutDialogInfo()
        info.SetIcon(imgdata.catalog['icon128'].GetIcon())
        info.Name = odemis.__shortname__
        info.Version = odemis.__version__
        info.Description = odemis.__fullname__
        info.Copyright = odemis.__copyright__
        info.WebSite = ("http://delmic.com", "delmic.com")
        info.Licence = odemis.__licensetxt__
        info.Developers = odemis.__authors__
        # info.DocWriter = '???'
        # info.Artist = '???'
        # info.Translator = '???'

        if DyeDatabase:
            info.Developers += ["", "Dye database from http://fluorophores.org"]
            info.Licence += ("""
The dye database is provided as-is, from the Fluorobase consortium.
The Fluorobase consortium provides this data and software in good faith, but
akes no warranty, expressed or implied, nor assumes any legal liability or
responsibility for any purpose for which they are used. For further information
see http://www.fluorophores.org/disclaimer/.
""")
        wx.AboutBox(info)

    def _on_manual(self, evt):
        gc = odemis.gui.conf.get_general_conf()
        subprocess.Popen(['xdg-open', gc.get_manual(self._main_data.role)])

    def _on_dev_manual(self, evt):
        gc = odemis.gui.conf.get_general_conf()
        subprocess.Popen(['xdg-open', gc.get_dev_manual()])

    def _on_inspect(self, evt):
        from wx.lib.inspection import InspectionTool
        InspectionTool().Show()
#
#     def on_htmldoc(self, evt):
#         """ Launch Python's SimpleHTTPServer in a separate process and have it
#         serve the source code documentation as created by Sphinx
#         """
#         self.http_proc = subprocess.Popen(
#             ["python", "-m", "SimpleHTTPServer"],
#             stderr=subprocess.STDOUT,
#             stdout=subprocess.PIPE,
#             cwd=os.path.dirname(odemis.gui.conf.get_general_conf().html_dev_doc))
#
#         import webbrowser
#         webbrowser.open('http://localhost:8000')


    @call_after
    def _on_debug_va(self, enabled):
        """ Update the debug menu check """
        self._main_frame.menu_item_debug.Check(enabled)

    def _on_debug_menu(self, evt):
        """ Update the debug VA according to the menu
        """
        # TODO: use event?
        self._main_data.debug.value = self._main_frame.menu_item_debug.IsChecked()
