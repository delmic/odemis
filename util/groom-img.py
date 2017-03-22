#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import division, absolute_import, print_function

import StringIO
import argparse
import glob
import os
import subprocess
import sys
from wx.tools.img2py import img2py

# Directories that contain images to embed into data.py
img_dirs = (".", "button", "icon", "menu")


def cmd_exists(cmd):
    return subprocess.call("type " + cmd,
                           shell=True,
                           stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE) == 0

parser = argparse.ArgumentParser(description='Recursively compile all PNG images into a Python '
                                             'module')
#parser.add_argument("-o", "--optimize", help="Optimize PNG images", action='store_true')
parser.add_argument("-s", "--skiplarge", help="Skip 'large' files", action='store_true')
args = parser.parse_args()

base_dir = os.path.join(os.path.dirname(__file__), "../src/")

# PNG optimisation

#if args.optimize:
if not cmd_exists('pngcrush'):
    print "Pngcrush not found, can't optimize!"
else:
    for dirpath, dirnames, filenames in os.walk(base_dir):
        print "** Optimizing", dirpath

        for f in [fn for fn in filenames if fn[-4:] == '.png']:
            ff = os.path.join(dirpath, f)
            fs = os.path.getsize(ff)

            if not args.skiplarge or fs < 10240:
                print ' - ', ff
                subprocess.call(['pngcrush', '-brute', '-rem', 'alla', ff, '%s.opt' % ff],
                                stdout=subprocess.PIPE)
                if os.path.exists('%s.opt' % ff):
                    os.rename('%s.opt' % ff, ff)
                else:
                    print "    %s.opt not found!!" % ff
            else:
                print ' - SKIPPING ', ff

# Image embedding
#first = True
#fakeoutput = StringIO.StringIO()  # for img2py

#if not cmd_exists('img2py'):
#    print "Img2py not found, can't generate python file!"
#else:
#    outpy = os.path.join(base_dir, 'data.py')
#    for idir in img_dirs:
#        dirpath = os.path.join(base_dir, idir)
#        print "** Packaging", dirpath

#        for f in glob.glob(os.path.join(dirpath, "*.png")):
#            print ' - ', f

#            sys.stdout = fakeoutput  # because img2py prints useless info uncontrollably
#            img2py(f, outpy, append=(not first), catalog=True, functionCompatible=True)
#            first = False
#            sys.stdout = sys.__stdout__
