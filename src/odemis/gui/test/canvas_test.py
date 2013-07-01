#-*- coding: utf-8 -*-

"""
@author: Rinze de Laat

Copyright © 2013 Rinze de Laat, Delmic

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

#===============================================================================
# Test module for Odemis' gui.comp.buttons module
#===============================================================================

import unittest
import odemis.gui.comp.canvas as canvas

SCALES = [0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0, 128.0, 256.0]

VIEW_SIZE = (400, 400)

# View coordinates, with a top-left 0,0 origin
VIEW_COORDS = [
                (0, 0),
                (0, 349),
                (123, 0),
                (321, 322),
              ]

# Margin around the view
MARGINS = [(0, 0), (512, 512)]

# Buffer coordinates, with a top-left 0,0 origin
BUFF_COORDS = [
                (0, 0),
                (0, 349),
                (512 + 200, 512 + 200),
                (133, 0),
                (399, 399),
              ]

# The center of the buffer, in world coordinates
BUFFER_CENTER = [(0.0, 0.0)]

class CanvasTestCase(unittest.TestCase):


    def test_buffer_to_world(self):

        for m in MARGINS:
            offset = tuple((x / 2) + y for x, y in zip(VIEW_SIZE, m))
            for bp in BUFF_COORDS:
                for s in SCALES:
                    for c in BUFFER_CENTER:
                        wp = canvas.buffer_to_world_pos(bp, c, s, offset)
                        nbp = canvas.world_to_buffer_pos(wp, c, s, offset)

                        err = ("{} -> {} -> {} "
                               "scale: {}, center: {}, offset: {}")
                        err = err.format(bp, wp, nbp, s, c, offset)
                        print err

                        self.assertAlmostEqual(bp[0], nbp[0], msg=err)
                        self.assertAlmostEqual(bp[1], nbp[1], msg=err)

if __name__ == "__main__":
    unittest.main()
