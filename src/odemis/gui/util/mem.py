# -*- coding: utf-8 -*-
"""
Created on 16 Mar 2016

@author: Rinze de Laat

Copyright © 2016 Rinze de Laat, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

"""


def memory_usage_psutil():
    """ Return the memory usage in Megabytes or None if psutil is not installed """
    try:
        import os
        import psutil
        process = psutil.Process(os.getpid())
        mem = process.memory_info()[0] / float(2 ** 20)
        return mem
    except ImportError:
        return 0
