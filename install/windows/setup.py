#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Setup script to build Cython scripts on Windows

Run `python setup.py build_ext --inplace` to compile

This file is also automatically called from the Odemis Viewer Build script

"""

from glob import glob
import os
from distutils.core import setup
from distutils import msvc9compiler

import numpy as np
from Cython.Build import cythonize

os.chdir(os.path.join("../../src", os.path.dirname(__file__)))

# The default function in distutils has trouble finding the vcvarsall.bat file, so we override
# the function responsible for that

def find_vcvarsall(_=None):
    from os.path import expanduser
    home = expanduser("~")

    # Adjust the following variable as needed.
    productdir = "%s/AppData/Local/Programs/Common/Microsoft/Visual C++ for Python/9.0" % home
    vcvarsall = os.path.join(productdir, "vcvarsall.bat")
    if os.path.isfile(vcvarsall):
        print "VCvarsall.bat found"
        return vcvarsall
    else:
        print "Vcvarsall.bat not found. Update the productdir variable in setup.py?"
        return None

msvc9compiler.find_vcvarsall = find_vcvarsall

setup(
    name='ImageFast',
    include_dirs=[np.get_include()],
    ext_modules=cythonize(glob(os.path.join("odemis\\util\\*.pyx"))),
)
