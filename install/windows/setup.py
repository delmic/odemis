#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Setup script to build Cython scripts on Windows

Run `python setup.py build_ext --inplace` to compile

This file is also automatically called from the Odemis Viewer Build script

"""

from Cython.Build import cythonize
from distutils import msvc9compiler
from distutils.core import setup
import glob
import numpy
import os

os.chdir(os.path.join(os.path.dirname(__file__), "..", "..", "src"))

setup(
    name='ImageFast',
    ext_modules=cythonize(glob.glob(os.path.join("odemis", "util", "*.pyx"))),
    include_dirs=[numpy.get_include()],
)
