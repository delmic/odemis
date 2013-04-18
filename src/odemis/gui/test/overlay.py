#-*- coding: utf-8 -*-

"""
@author: Rinze de Laat

Copyright © 2013 Rinze de Laat, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License as published by the Free Software
Foundation, either version 2 of the License, or (at your option) any later
version.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General P*ublic License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

"""

#===============================================================================
# Test module for Odemis' gui.comp.buttons module
#===============================================================================

# import random
import itertools
import os
import unittest

if os.getcwd().endswith('test'):
    os.chdir('../..')
    print "Working directory changed to", os.getcwd()

import odemis.gui.comp.overlay as ol

# Bounding box clipping test data generation

def tp(trans, *ps):
    """ Translate points ps using trans """
    r = []
    for p in ps:
        r.append(tuple([vp + vt for vp, vt in zip(p, trans)]))
    return tuple(r)

# First we define a 2x2 bounding boxes, at different locations
bounding_boxes = [((-2, -2), (0, 0)), ((-1, -1), (1, 1)), ((0, 0), (2, 2)), ((2, 2), (4, 4))]

# From this, we generate 2x2 boxes that are situated all around these
# bounding boxes, but that do not touch or overlap them.

def relative_boxes(tl, br):

    t_left = [(-3, i) for i in range(-3, 4)]
    to_the_left = [tp(t, tl, br) for t in t_left]

    t_top = [(i, -3) for i in range(-3, 4)]
    to_the_top = [tp(t, tl, br) for t in t_top]

    t_right = [(3, i) for i in range(-3, 4)]
    to_the_right = [tp(t, tl, br) for t in t_right]

    t_bottom = [(i, 3) for i in range(-3, 4)]
    to_the_bottom = [tp(t, tl, br) for t in t_bottom]

    outside_boxes = to_the_left + to_the_top + to_the_right + to_the_bottom

    # Selection boxes that touch the outside of the bounding box
    touch_left = [tp((1, 0), tl, br) for tl, br in to_the_left[1:-1]]
    touch_top = [tp((0, 1), tl, br) for tl, br in to_the_top[1:-1]]
    touch_right = [tp((-1, 0), tl, br) for tl, br in to_the_right[1:-1]]
    touch_bottom = [tp((0, -1), tl, br) for tl, br in to_the_bottom[1:-1]]

    touching_boxes = touch_left + touch_top + touch_right + touch_bottom

    # Partial overlapping boxes
    overlap_left = [tp((1, 0), tl, br) for tl, br in touch_left[1:-1]]
    overlap_top = [tp((0, 1), tl, br) for tl, br in touch_top[1:-1]]
    overlap_right = [tp((-1, 0), tl, br) for tl, br in touch_right[1:-1]]
    overlap_bottom = [tp((0, -1), tl, br) for tl, br in touch_bottom[1:-1]]

    overlap_boxes = overlap_left + overlap_top + overlap_right + overlap_bottom

    return outside_boxes, touching_boxes, overlap_boxes

class CanvasTestCase(unittest.TestCase):

    def setUp(self):
        pass

    def tearDown(self):
        pass

    def test_clipping(self):

        tmp = "{}: {}x{} / {}x{} -> {}"

        for btl, bbr in bounding_boxes:

            outside, touching, overlap =  relative_boxes(btl, bbr)

            for tl, br in outside:
                r = ol.Overlay._clip(tl, br, btl, bbr)
                msg = tmp.format("outside", tl, br, btl, bbr, r)
                self.assertIsNone(r, msg)

            for tl, br in touching:
                r = ol.Overlay._clip(tl, br, btl, bbr)
                msg = tmp.format("touching", tl, br, btl, bbr, r)
                self.assertIsNone(r, msg)

            for tl, br in overlap:
                r = ol.Overlay._clip(tl, br, btl, bbr)
                msg = tmp.format("overlap", tl, br, btl, bbr, r)
                self.assertIsNotNone(r, msg)

                # 'Manual' checks
                if (btl, bbr) == ((-1, -1), (1, 1)):
                    if tl == (-2, -2):
                        self.assertEqual(r, ((-1, -1), (0, 0)), msg)
                    elif tl == (0, -1):
                        self.assertEqual(r, ((0, -1), (1, 1)), msg)
                    elif tl == (0, 0):
                        self.assertEqual(r, ((0, 0), (1, 1)), msg)

            # full and exact overlap
            tl, br = btl, bbr
            r = ol.Overlay._clip(tl, br, btl, bbr)
            self.assertEqual(r, (btl, bbr))

            # inner overlap
            tl, br = (btl[0] + 1, btl[1] + 1), bbr
            r = ol.Overlay._clip(tl, br, btl, bbr)
            self.assertEqual(r, ((btl[0] + 1, btl[1] + 1), bbr))

            # overflowing overlap
            tl, br = (btl[0] - 1, btl[1] - 1), (bbr[0] + 1, bbr[1] + 1)
            r = ol.Overlay._clip(tl, br, btl, bbr)
            self.assertEqual(r, (btl, bbr))

if __name__ == "__main__":
    unittest.main()
