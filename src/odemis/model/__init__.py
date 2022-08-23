# -*- coding: utf-8 -*-
'''
Created on 26 Mar 2012

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
# Load the package namespace here so that it's possible to just do "import model"

# to hide the lower layer
from Pyro4.core import oneway, isasync

from ._futures import *
from ._vattributes import *
from ._components import *
from ._dataflow import *
from ._core import *
from ._metadata import *
from ._dataio import *


#__all__ = []
#import model._properties
#__all__ += [name for name in dir(model._properties) if not name.startswith('_')]


# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell:
