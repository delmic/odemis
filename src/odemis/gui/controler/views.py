# -*- coding: utf-8 -*-
'''
Created on 1 Oct 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or
modify it under the terms of the GNU General Public License as published by the
Free Software Foundation, either version 2 of the License, or (at your option)
any later version.

Odemis is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY
or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
'''
from odemis.gui import instrmodel
from odemis.gui.log import log

class ViewController(object):
    """
    Manages the microscope view updates, change of viewport focus, etc.
    """
    
    def __init__(self, micgui, main_frame):
        '''
        micgui (MicroscopeGUI): the representation of the microscope GUI
        main_frame: (wx.Frame): the frame which contains the 4 viewports
        '''
        self._microscope = micgui
        self._main_frame = main_frame
        
        # TODO create the (default) views and set currentView
        
        
        # list of all the viewports (widgets that show the views)
        self._viewports = [main_frame.pnl_view_tl, main_frame.pnl_view_tr,
                           main_frame.pnl_view_bl, main_frame.pnl_view_br]
        
        # subscribe to layout and view changes
        self._microscope.viewLayout.subscribe(self.onViewLayout, init=True)
        self._microscope.currentView.subscribe(self.onView, init=True)
        
        
    def onView(self, view):
        """
        Called when another view is focused
        """ 
        log.debug("Changing focus to view %s", view.name.value)
        self._main_frame.pnl_tab_live.Freeze() # FIXME needed?
        for v in self._viewports:
            if v.view == view:
                v.SetFocus(True)
            else:
                v.SetFocus(False)
        self._main_frame.pnl_tab_live.Layout() # FIXME needed?
        self._main_frame.pnl_tab_live.Thaw()
        
    def onViewLayout(self, layout):
        """
        Called when the view layout of the GUI must be changed
        """
        # only called when changed
        if layout == instrmodel.VIEW_LAYOUT_ONE:
            self._main_frame.pnl_tab_live.Freeze()
            log.debug("Showing only one view")

            for v in self._viewports:
                if v.view == self._microscope.currentView.value:
                    v.Show()
                else:
                    v.Hide()

            self._main_frame.pnl_tab_live.Layout()
            self._main_frame.pnl_tab_live.Thaw()
        elif layout == instrmodel.VIEW_LAYOUT_22:
            log.debug("Showing all views")
    
            self._main_frame.pnl_tab_live.Freeze()
    
            for v in self._viewports:
                v.Show()
    
            self._main_frame.pnl_tab_live.Layout()
            self._main_frame.pnl_tab_live.Thaw()
        elif layout == instrmodel.VIEW_LAYOUT_FULLSCREEN:
            raise NotImplementedError()
        else:
            raise NotImplementedError()
        
