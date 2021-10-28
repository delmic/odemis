#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on 20 Jul 2021

Copyright © 2021 Philip Winkler, Delmic

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

from odemis import model
import time

_executor = model.CancellableThreadPoolExecutor(max_workers=1)


def align(main_data):
    """
    :param main_data (odemis.gui.model.FastEMMainGUIData):
    :returns: (ProgressiveFuture): acquisition future
    """
    f = model.ProgressiveFuture()
    _executor.submitf(f, _run_fake_alignment)
    return f


def _run_fake_alignment():
    time.sleep(2)
