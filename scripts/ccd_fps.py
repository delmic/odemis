#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''
Created on 6 Feb 2015

@author: Éric Piel

Copyright © 2015 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''

# This script collects images from the CCD as fast as possible and estimate the acquisition speed in frame per seconds (FPS)

# It uses the current binning and resolution of the CCD. To set it, you can use:
# odemis-cli --set-attr ccd binning 16,16
# odemis-cli --set-attr ccd resolution 1,1

import logging
from odemis import model
import sys
import time


n = 0

def _on_image(df, data):
    global n
    n += 1


def main(args):
    """
    Handles the command line arguments
    args is the list of arguments passed
    return (int): value to return to the OS as program exit code
    """
    global n
    ccd = model.getComponent(role="ccd")


    # TODO: decrease exposure time until it seems to not follow?
    # How to detects drops?
    fps_goal = 10000 # Hz
    ccd.exposureTime.value = 1 / fps_goal # s

    prev_n = n
    ccd.data.subscribe(_on_image)
    try:
        for i in range(100):
            time.sleep(1)
            newn = n
            fps = newn - prev_n
            prev_n = newn
            print("%d fps" % fps)
    except KeyboardInterrupt:
        pass
    except Exception:
        logging.exception("Failed to estimate FPS due to error")
    finally:
        ccd.data.unsubscribe(_on_image)

    return 0

if __name__ == '__main__':
    ret = main(sys.argv)
    exit(ret)
