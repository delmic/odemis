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

class ViewController(object):
    """
    Manages the microscope view updates, change of focus, etc.
    """
    
    def __init__(self, micgui, main_frame):
        '''
        micgui (MicroscopeGUI): the representation of the microscope GUI
        '''
        self._microscope = micgui
        self._main_frame = main_frame
        
        # subscribe to layout and view changes
        
        # subscribe to each microscopeview lastupdate, and call canvas.shouldUpdateDrawing())