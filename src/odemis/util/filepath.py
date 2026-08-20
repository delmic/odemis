# -*- coding: utf-8 -*-
"""
Created on 20 Aug 2026

@author: Tim Moerkerken

Copyright © 2026 Tim Moerkerken, Delmic

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
import os
import sys
from pathlib import Path
from typing import Union


def is_relative_to(path: Path, *base_path: Union[str, os.PathLike]) -> bool:
    """Determine whether provided path is relative to the specified base path.

    :param path: The path to check for.
    :param *base_path: One or more path segments or path-like objects to compare against.
    :return: True if the provided path is subpath to the base path, otherwise False.

    NOTE: Method is for backwards compatibility with Ubuntu 20.04's Python 3.8. Remove it once 20.04 is phased out.
    """
    if sys.version_info >= (3, 9):
        return path.is_relative_to(*base_path)
    try:
        path.relative_to(*base_path)
        return True
    except ValueError:
        return False
