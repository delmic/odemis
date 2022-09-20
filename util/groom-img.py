#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Prepare the images to be embedded in a Python program. To be called every time
# more images are added to the source. In practice, it just goes through PNG
# images, and optimize them for size. (It used to do more, but that's not needed anymore.)
# Call like:
# ./util/groom-img.py -s src/odemis/gui/img/

import argparse
import os
import subprocess
import sys


def cmd_exists(cmd):
    return subprocess.call("type " + cmd,
                           shell=True,
                           stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE) == 0


def main(args):
    parser = argparse.ArgumentParser(description='Recursively optimize all PNG images')
    #parser.add_argument("-o", "--optimize", help="Optimize PNG images", action='store_true')
    parser.add_argument("-s", "--skiplarge", help="Skip 'large' files", action='store_true')
    parser.add_argument("dir", help="Base directory (default to src/)", default="src/", nargs="?")
    args = parser.parse_args()

    if not cmd_exists('pngcrush'):
        print("pngcrush not found, can't optimize!. Install it with \"sudo apt install pngcrush\"")
        return 1

    for dirpath, dirnames, filenames in os.walk(args.dir):
        print("** Optimizing", dirpath)

        for f in [fn for fn in filenames if fn[-4:] == '.png']:
            ff = os.path.join(dirpath, f)

            if not args.skiplarge or os.path.getsize(ff) < 10240:
                print(' - ', ff)
                subprocess.check_call(['pngcrush', '-brute', '-rem', 'alla', ff, '%s.opt' % ff])
                if os.path.exists('%s.opt' % ff):
                    os.rename('%s.opt' % ff, ff)
                else:
                    print("    %s.opt not found!" % ff)
            else:
                print(' - SKIPPING ', ff)

    return 0


if __name__ == '__main__':
    ret = main(sys.argv)
    exit(ret)
