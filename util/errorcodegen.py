#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
Created on 15 Mar 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms 
of the GNU General Public License version 2 as published by the Free Software 
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; 
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR 
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with 
Odemis. If not, see http://www.gnu.org/licenses/.
'''
"""
Generates error code python array from an include file.
Only there to simply updating the driver.
"""

import fileinput

for line in fileinput.input():
    # look for lines like:
    # #define DRV_ERROR_CODES 20001

    words = line.strip().split(" ")
#    print words
    if (len(words) == 3 and words[0] == "#define" and 
        "_" in words[1] and words[2].isdigit()):
        # and generates something like:
        # 20001: "DRV_ERROR_CODES",
        print("%s: \"%s\"," % (words[2], words[1]))
        
    
